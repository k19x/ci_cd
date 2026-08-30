#!/usr/bin/env python3
"""
Detecta endpoints de API definidos no código-fonte mas ausentes na spec OpenAPI
(shadow APIs) via análise estática multi-framework.

Uso:
    python scripts/shadow_api.py <repo_dir> --output shadow_api.sarif [--spec path/to/spec.yaml]

Quando --spec não é passado, o script tenta encontrar specs automaticamente.
Se nenhuma spec for encontrada, lista todos os endpoints descobertos como 'info'
(não bloqueia o gate). Se spec existir, endpoints não documentados viram 'warning'
(medium no normalize.py).
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Padrões de rota por framework
# Cada entrada: (framework, extensões, regex, grupo_method, grupo_path)
# grupo_method=None → método desconhecido/múltiplos
# ---------------------------------------------------------------------------
PATTERNS = [
    # Python – FastAPI / Starlette
    ("fastapi",  [".py"],          r'@\w+\.(get|post|put|patch|delete|head|options|websocket)\s*\(\s*["\']([^"\']+)["\']',  1, 2),
    # Python – Flask / Quart / Blueprint
    ("flask",    [".py"],          r'@\w+\.route\s*\(\s*["\']([^"\']+)["\']',                                               None, 1),
    # Python – Django urls.py  (re_path / path)
    ("django",   [".py"],          r'(?:re_)?path\s*\(\s*["\']([^"\']+)["\']',                                              None, 1),
    # JavaScript / TypeScript – Express
    ("express",  [".js",".ts",".mjs",".cjs"],
                                   r'(?:app|router)\.(get|post|put|patch|delete|head|options)\s*\(\s*["`\']([^"`\']+)["`\']', 1, 2),
    # JavaScript / TypeScript – Fastify
    ("fastify",  [".js",".ts"],    r'fastify\.(get|post|put|patch|delete|head|options)\s*\(\s*["`\']([^"`\']+)["`\']',       1, 2),
    # JavaScript / TypeScript – Hapi
    ("hapi",     [".js",".ts"],    r'server\.route\s*\(.*?method\s*:\s*["\']([A-Z]+)["\'].*?path\s*:\s*["\']([^"\']+)["\']', 1, 2),
    # Go – Gin
    ("gin",      [".go"],          r'(?:\w+)\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(\s*"([^"]+)"',                   1, 2),
    # Go – Chi / net/http / Gorilla Mux
    ("go-mux",   [".go"],          r'(?:r|router|mux|m)\.(Get|Post|Put|Patch|Delete|Head|Options|HandleFunc)\s*\(\s*"([^"]+)"', 1, 2),
    # Java – Spring (annotations)
    ("spring",   [".java"],        r'@(?:Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']', None, 1),
    # Ruby – Rails routes.rb
    ("rails",    [".rb"],          r'(?:get|post|put|patch|delete)\s+["\']([^"\']+)["\']',                                  None, 1),
    # PHP – Laravel
    ("laravel",  [".php"],         r'Route::(get|post|put|patch|delete|any)\s*\(\s*["\']([^"\']+)["\']',                    1, 2),
    # Rust – Actix-web
    ("actix",    [".rs"],          r'#\[(?:get|post|put|patch|delete|head|options)\s*\(\s*"([^"]+)"\s*\)\]',                None, 1),
    # C# – ASP.NET minimal APIs
    ("aspnet",   [".cs"],          r'app\.(MapGet|MapPost|MapPut|MapPatch|MapDelete)\s*\(\s*["\']([^"\']+)["\']',           1, 2),
]

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "vendor", ".venv", "venv",
    "dist", "build", "target", ".tox", "coverage", "test", "tests",
    "spec", "specs", "__tests__", "e2e", "fixtures", ".github",
}

SKIP_FILE_PATTERNS = re.compile(r'(test|spec|mock|fake|stub|fixture)', re.I)


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if SKIP_FILE_PATTERNS.search(path.stem):
        return True
    return False


def find_routes(repo_dir: Path) -> list[dict]:
    """Varre o repo e retorna lista de {framework, method, path, file, line}."""
    # Indexa arquivos por extensão para não varrer tudo para cada padrão
    ext_files: dict[str, list[Path]] = {}
    for f in repo_dir.rglob("*"):
        if f.is_file() and not _should_skip(f.relative_to(repo_dir)):
            ext_files.setdefault(f.suffix.lower(), []).append(f)

    routes: list[dict] = []
    seen: set[str] = set()

    for framework, exts, pattern, m_group, p_group in PATTERNS:
        rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        for ext in exts:
            for fpath in ext_files.get(ext, []):
                try:
                    src = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for match in rx.finditer(src):
                    method = (match.group(m_group).upper() if m_group and m_group <= len(match.groups()) else "ANY")
                    path_val = (match.group(p_group) if p_group and p_group <= len(match.groups()) else "")
                    if not path_val or not path_val.startswith("/"):
                        continue
                    # Linha aproximada
                    line = src[:match.start()].count("\n") + 1
                    key = f"{method}:{path_val}:{str(fpath)}"
                    if key in seen:
                        continue
                    seen.add(key)
                    routes.append({
                        "framework": framework,
                        "method": method,
                        "path": path_val,
                        "file": str(fpath.relative_to(repo_dir)),
                        "line": line,
                    })
    return routes


def load_spec_paths(spec_file: Path) -> set[str]:
    """Extrai o conjunto de paths documentados de uma spec OpenAPI/Swagger."""
    try:
        text = spec_file.read_text(encoding="utf-8", errors="ignore")
        if spec_file.suffix in (".yaml", ".yml"):
            if _HAS_YAML:
                data = _yaml.safe_load(text)
            else:
                # Fallback: extrai paths com regex simples
                paths = re.findall(r'^\s{2}(/[^\s:]+)\s*:', text, re.MULTILINE)
                return {p.rstrip("/") for p in paths}
        else:
            data = json.loads(text)
        paths = data.get("paths", {})
        return {p.rstrip("/") for p in paths}
    except Exception:
        return set()


def find_specs(repo_dir: Path) -> list[Path]:
    spec_rx = re.compile(r'(openapi|swagger)\s*[:\s]', re.I)
    found = []
    for ext in (".yaml", ".yml", ".json"):
        for f in repo_dir.rglob(f"*{ext}"):
            if _should_skip(f.relative_to(repo_dir)):
                continue
            try:
                snippet = f.read_text(encoding="utf-8", errors="ignore")[:512]
                if spec_rx.search(snippet):
                    found.append(f)
            except OSError:
                pass
    return found


def _normalize_path(p: str) -> str:
    """Normaliza parâmetros de rota para comparação: {id} ≈ :id ≈ <id> → {param}."""
    p = re.sub(r':\w+', '{param}', p)
    p = re.sub(r'<[^>]+>', '{param}', p)
    p = re.sub(r'\{[^}]+\}', '{param}', p)
    return p.rstrip("/")


def make_sarif(routes: list[dict], shadow: bool, repo_dir: Path) -> dict:
    """Gera SARIF 2.1.0 para os routes fornecidos."""
    level = "warning" if shadow else "note"
    rule_id = "shadow-api/undocumented-endpoint" if shadow else "shadow-api/discovered-endpoint"
    rule_name = "UndocumentedAPIEndpoint" if shadow else "DiscoveredAPIEndpoint"
    rule_desc = (
        "Endpoint de API encontrado no código-fonte não está documentado na spec OpenAPI. "
        "Endpoints não documentados ('shadow APIs') podem conter vulnerabilidades não auditadas."
        if shadow else
        "Endpoint de API encontrado no código-fonte. Nenhuma spec OpenAPI presente para comparação."
    )

    results = []
    for r in routes:
        msg = (
            f"Shadow API: {r['method']} {r['path']} ({r['framework']}) não está na spec OpenAPI."
            if shadow else
            f"Endpoint descoberto: {r['method']} {r['path']} ({r['framework']})."
        )
        fp_raw = f"{rule_id}:{r['file']}:{r['path']}:{r['method']}"
        fingerprint = hashlib.sha256(fp_raw.encode()).hexdigest()[:16]
        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {"text": msg},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": r["file"].replace("\\", "/"), "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": r["line"]},
                }
            }],
            "fingerprints": {"shadow-api/v1": fingerprint},
            "properties": {
                "framework": r["framework"],
                "http_method": r["method"],
                "api_path": r["path"],
            },
        })

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "shadow-api-scanner",
                    "version": "1.0.0",
                    "informationUri": "https://github.com/k19x/ci_cd",
                    "rules": [{
                        "id": rule_id,
                        "name": rule_name,
                        "shortDescription": {"text": rule_name},
                        "fullDescription": {"text": rule_desc},
                        "defaultConfiguration": {"level": level},
                        "properties": {"security-severity": "5.3" if shadow else "0.0"},
                    }],
                }
            },
            "results": results,
            "artifacts": [{"location": {"uri": r["file"].replace("\\", "/"), "uriBaseId": "%SRCROOT%"}}
                          for r in routes],
        }],
    }


def main():
    ap = argparse.ArgumentParser(description="Shadow API detector")
    ap.add_argument("repo_dir", help="Diretório raiz do repositório a escanear")
    ap.add_argument("--output", default="shadow_api.sarif", help="Arquivo SARIF de saída")
    ap.add_argument("--spec", help="Caminho para spec OpenAPI/Swagger (opcional; detectado automaticamente se omitido)")
    args = ap.parse_args()

    repo = Path(args.repo_dir).resolve()
    if not repo.is_dir():
        print(f"[shadow-api] ERRO: {repo} não é um diretório", file=sys.stderr)
        sys.exit(1)

    print(f"[shadow-api] Varrendo {repo} ...", file=sys.stderr)
    routes = find_routes(repo)
    print(f"[shadow-api] {len(routes)} endpoint(s) encontrado(s) no código", file=sys.stderr)

    # Localiza spec
    spec_paths: set[str] = set()
    spec_file = Path(args.spec) if args.spec else None
    if spec_file is None:
        specs = find_specs(repo)
        if specs:
            spec_file = specs[0]
            print(f"[shadow-api] Spec detectada: {spec_file}", file=sys.stderr)

    if spec_file and spec_file.is_file():
        spec_paths = load_spec_paths(spec_file)
        normalized_spec = {_normalize_path(p) for p in spec_paths}
        print(f"[shadow-api] {len(spec_paths)} path(s) na spec", file=sys.stderr)

        shadow_routes = [
            r for r in routes
            if _normalize_path(r["path"]) not in normalized_spec
        ]
        print(f"[shadow-api] {len(shadow_routes)} shadow API(s) (não documentado(s))", file=sys.stderr)
        sarif = make_sarif(shadow_routes, shadow=True, repo_dir=repo)
    else:
        print("[shadow-api] Nenhuma spec OpenAPI encontrada — listando endpoints como info", file=sys.stderr)
        sarif = make_sarif(routes, shadow=False, repo_dir=repo)

    out = Path(args.output)
    out.write_text(json.dumps(sarif, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[shadow-api] SARIF gravado em {out}  ({len(sarif['runs'][0]['results'])} result(s))", file=sys.stderr)


if __name__ == "__main__":
    main()
