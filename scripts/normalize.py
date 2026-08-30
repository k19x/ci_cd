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
import re
import sys
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]

LEVEL_TO_SEVERITY = {"error": "high", "warning": "medium", "note": "low", "none": "info"}

# ── Compliance: CWE → OWASP Top 10 (2021) ─────────────────────────────────
_CWE_RE   = re.compile(r"cwe[-_ /]{0,2}(\d{1,4})", re.I)
_OWASP_RE = re.compile(r"\bA(\d{1,2}):(\d{4})", re.I)

CWE_TO_OWASP = {
    # A01 Broken Access Control
    22: "A01", 23: "A01", 35: "A01", 59: "A01", 200: "A01", 201: "A01",
    284: "A01", 285: "A01", 352: "A01", 425: "A01", 639: "A01", 862: "A01", 863: "A01",
    # A02 Cryptographic Failures
    261: "A02", 296: "A02", 310: "A02", 319: "A02", 321: "A02", 322: "A02",
    323: "A02", 324: "A02", 325: "A02", 326: "A02", 327: "A02", 328: "A02",
    329: "A02", 330: "A02", 331: "A02", 335: "A02", 336: "A02", 337: "A02",
    338: "A02", 340: "A02", 347: "A02", 759: "A02", 760: "A02", 780: "A02", 916: "A02",
    # A03 Injection
    20: "A03", 74: "A03", 75: "A03", 77: "A03", 78: "A03", 79: "A03", 80: "A03",
    83: "A03", 87: "A03", 88: "A03", 89: "A03", 90: "A03", 91: "A03", 93: "A03",
    94: "A03", 95: "A03", 96: "A03", 97: "A03", 98: "A03", 99: "A03", 113: "A03",
    116: "A03", 138: "A03", 184: "A03", 470: "A03", 471: "A03", 564: "A03",
    610: "A03", 643: "A03", 644: "A03", 652: "A03", 917: "A03",
    # A04 Insecure Design
    73: "A04", 183: "A04", 209: "A04", 213: "A04", 235: "A04", 256: "A04",
    257: "A04", 266: "A04", 269: "A04", 280: "A04", 311: "A04", 312: "A04",
    313: "A04", 316: "A04", 419: "A04", 430: "A04", 434: "A04", 444: "A04",
    451: "A04", 472: "A04", 501: "A04", 522: "A04", 525: "A04", 539: "A04",
    579: "A04", 598: "A04", 602: "A04", 642: "A04", 646: "A04", 650: "A04",
    653: "A04", 656: "A04", 657: "A04", 799: "A04", 807: "A04", 840: "A04",
    841: "A04", 927: "A04", 1021: "A04", 1173: "A04",
    # A05 Security Misconfiguration
    2: "A05", 11: "A05", 13: "A05", 15: "A05", 16: "A05", 260: "A05",
    315: "A05", 520: "A05", 526: "A05", 537: "A05", 541: "A05", 547: "A05",
    611: "A05", 614: "A05", 756: "A05", 776: "A05", 942: "A05", 1004: "A05",
    1032: "A05", 1174: "A05",
    # A06 Vulnerable and Outdated Components
    937: "A06", 1035: "A06", 1104: "A06",
    # A07 Identification and Authentication Failures
    255: "A07", 259: "A07", 287: "A07", 288: "A07", 290: "A07", 294: "A07",
    295: "A07", 297: "A07", 300: "A07", 302: "A07", 304: "A07", 306: "A07",
    307: "A07", 346: "A07", 384: "A07", 521: "A07", 613: "A07", 620: "A07",
    640: "A07", 798: "A07", 940: "A07", 1216: "A07",
    # A08 Software and Data Integrity Failures
    345: "A08", 353: "A08", 426: "A08", 494: "A08", 502: "A08", 565: "A08", 784: "A08",
    829: "A08", 830: "A08", 915: "A08",
    # A09 Security Logging and Monitoring Failures
    117: "A09", 223: "A09", 532: "A09", 778: "A09",
    # A10 SSRF
    918: "A10",
}

OWASP_NAMES = {
    "A01": "A01:2021 Broken Access Control",
    "A02": "A02:2021 Cryptographic Failures",
    "A03": "A03:2021 Injection",
    "A04": "A04:2021 Insecure Design",
    "A05": "A05:2021 Security Misconfiguration",
    "A06": "A06:2021 Vulnerable Components",
    "A07": "A07:2021 Auth Failures",
    "A08": "A08:2021 Integrity Failures",
    "A09": "A09:2021 Logging Failures",
    "A10": "A10:2021 SSRF",
}


def compliance_of(rule: dict, tool: str) -> tuple[str, str]:
    """Extrai (cwe, owasp) das tags SARIF; mapeia CWE→OWASP quando faltar tag OWASP."""
    props = (rule or {}).get("properties", {}) or {}
    tags = [str(t) for t in (props.get("tags", []) or [])]
    blob = " ".join(tags) + " " + str(props.get("cwe", ""))

    cwe = ""
    m = _CWE_RE.search(blob)
    if m:
        cwe = f"CWE-{int(m.group(1))}"

    owasp = ""
    m = _OWASP_RE.search(blob)
    if m:
        owasp = f"A{int(m.group(1)):02d}"
    elif cwe:
        owasp = CWE_TO_OWASP.get(int(cwe.split("-")[1]), "")
    # dependências vulneráveis (Trivy CVEs) caem em A06 por definição
    if not owasp and tool.lower().startswith("trivy"):
        owasp = "A06"
    return cwe, (OWASP_NAMES.get(owasp, "") if owasp else "")


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
            cwe, owasp = compliance_of(rule, tool)
            findings.append(
                {
                    "id": fingerprint(tool, rule_id, path, snippet or message),
                    "tool": tool,
                    "rule": rule_id,
                    "severity": severity_of(result, rule, tool),
                    "file": path,
                    "line": line,
                    "message": message[:500],
                    "cwe": cwe,
                    "owasp": owasp,
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
