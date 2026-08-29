#!/usr/bin/env python3
"""Normaliza um ou mais arquivos SARIF em um findings.json unificado.

- Extrai severidade do `security-severity` (score CVSS que Trivy/Semgrep gravam
  na regra) com fallback para o `level` do SARIF.
- Deduplica por fingerprint (regra + arquivo + trecho), então o mesmo achado
  reportado por duas ferramentas vira um único finding.

Uso: python normalize.py <dir-ou-arquivos-sarif...> --output findings.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]

LEVEL_TO_SEVERITY = {"error": "high", "warning": "medium", "note": "low", "none": "info"}


def score_to_severity(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "info"


def rule_index(run: dict) -> dict:
    rules = (run.get("tool", {}).get("driver", {}) or {}).get("rules", []) or []
    return {r.get("id"): r for r in rules if r.get("id")}


def severity_of(result: dict, rule: dict, tool: str) -> str:
    props = (rule or {}).get("properties", {}) or {}
    raw = props.get("security-severity")
    if raw is not None:
        try:
            return score_to_severity(float(raw))
        except (TypeError, ValueError):
            pass
    # Tags do Semgrep às vezes trazem a severidade original
    for tag in props.get("tags", []) or []:
        if tag.lower() in SEVERITIES:
            return tag.lower()
    # Segredo vazado é sempre crítico, independente do level
    if tool.lower().startswith("gitleaks"):
        return "critical"
    level = result.get("level") or (rule or {}).get("defaultConfiguration", {}).get("level", "warning")
    return LEVEL_TO_SEVERITY.get(level, "medium")


def location_of(result: dict) -> tuple[str, int]:
    for loc in result.get("locations", []) or []:
        phys = loc.get("physicalLocation", {}) or {}
        path = (phys.get("artifactLocation", {}) or {}).get("uri", "")
        line = (phys.get("region", {}) or {}).get("startLine", 0)
        if path:
            return path, line or 0
    return "", 0


def fingerprint(tool: str, rule_id: str, path: str, snippet: str) -> str:
    # Sem número de linha: uma linha em branco adicionada acima não gera achado "novo"
    return hashlib.sha256(f"{rule_id}|{path}|{snippet}".encode()).hexdigest()[:16]


def parse_sarif(file: Path) -> list[dict]:
    data = json.loads(file.read_text(encoding="utf-8"))
    findings = []
    for run in data.get("runs", []) or []:
        tool = (run.get("tool", {}).get("driver", {}) or {}).get("name", file.stem)
        rules = rule_index(run)
        for result in run.get("results", []) or []:
            rule_id = result.get("ruleId", "unknown")
            rule = rules.get(rule_id, {})
            path, line = location_of(result)
            message = (result.get("message", {}) or {}).get("text", "")
            snippet = ""
            for loc in result.get("locations", []) or []:
                region = (loc.get("physicalLocation", {}) or {}).get("region", {}) or {}
                snippet = (region.get("snippet", {}) or {}).get("text", "") or snippet
            findings.append(
                {
                    "id": fingerprint(tool, rule_id, path, snippet or message),
                    "tool": tool,
                    "rule": rule_id,
                    "severity": severity_of(result, rule, tool),
                    "file": path,
                    "line": line,
                    "message": message[:500],
                }
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="arquivos .sarif ou diretórios contendo-os")
    parser.add_argument("--output", default="findings.json")
    args = parser.parse_args()

    files: list[Path] = []
    for raw in args.inputs:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.sarif")))
        elif p.is_file():
            files.append(p)

    if not files:
        print("Nenhum arquivo SARIF encontrado; gerando findings.json vazio.")

    seen: dict[str, dict] = {}
    for f in files:
        try:
            for finding in parse_sarif(f):
                existing = seen.get(finding["id"])
                if existing:
                    tools = set(existing["tool"].split("+")) | {finding["tool"]}
                    existing["tool"] = "+".join(sorted(tools))
                    # mantém a severidade mais alta entre as ferramentas
                    if SEVERITIES.index(finding["severity"]) < SEVERITIES.index(existing["severity"]):
                        existing["severity"] = finding["severity"]
                else:
                    seen[finding["id"]] = finding
        except (json.JSONDecodeError, OSError) as exc:
            print(f"AVISO: ignorando {f}: {exc}", file=sys.stderr)

    findings = sorted(seen.values(), key=lambda x: (SEVERITIES.index(x["severity"]), x["file"]))
    summary = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in SEVERITIES}
    output = {"summary": summary, "count": len(findings), "findings": findings}
    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(findings)} finding(s) únicos gravados em {args.output}")
    print("Resumo: " + ", ".join(f"{sev}={n}" for sev, n in summary.items() if n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
