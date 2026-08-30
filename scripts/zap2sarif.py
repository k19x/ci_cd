#!/usr/bin/env python3
"""Converte o JSON do OWASP ZAP baseline scan em SARIF mínimo para o normalize.py.

Uso: python zap2sarif.py zap.json --output zap.sarif
"""

import argparse
import json
import sys
from pathlib import Path

RISK_TO_LEVEL = {"3": "error", "2": "error", "1": "warning", "0": "note"}
RISK_TO_SCORE = {"3": "9.1", "2": "7.5", "1": "5.0", "0": "2.0"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="zap.json gerado pelo zap-baseline.py -J")
    parser.add_argument("--output", default="zap.sarif")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print("zap.json não encontrado — gerando SARIF vazio.")
        data = {"site": []}
    else:
        data = json.loads(src.read_text(encoding="utf-8"))

    rules, results = {}, []
    for site in data.get("site", []) or []:
        target = site.get("@name", "")
        for alert in site.get("alerts", []) or []:
            rule_id = f"zap-{alert.get('pluginid', 'unknown')}"
            risk = str(alert.get("riskcode", "1"))
            cwe = alert.get("cweid", "")
            tags = ["dast"]
            if cwe and cwe != "-1":
                tags.append(f"CWE-{cwe}")
            if rule_id not in rules:
                rules[rule_id] = {
                    "id": rule_id,
                    "name": alert.get("name", rule_id),
                    "shortDescription": {"text": alert.get("name", rule_id)},
                    "properties": {"security-severity": RISK_TO_SCORE.get(risk, "5.0"),
                                   "tags": tags},
                }
            for inst in (alert.get("instances", []) or [])[:20]:
                uri = inst.get("uri", target) or target
                results.append({
                    "ruleId": rule_id,
                    "level": RISK_TO_LEVEL.get(risk, "warning"),
                    "message": {"text": (alert.get("name", "") + " — " +
                                         (alert.get("desc", "") or ""))[:400].replace("<p>", "").replace("</p>", " ")},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {"startLine": 1},
                    }}],
                })

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "OWASP ZAP", "rules": list(rules.values())}},
            "results": results,
        }],
    }
    Path(args.output).write_text(json.dumps(sarif, indent=2), encoding="utf-8")
    print(f"{len(results)} resultado(s) DAST convertidos para {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
