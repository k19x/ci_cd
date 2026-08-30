#!/usr/bin/env python3
"""Converte a saída JSON do Spectral (lint de OpenAPI/Swagger) em SARIF mínimo.

Uso: python spectral2sarif.py spectral.json --output api.sarif
"""

import argparse
import json
import sys
from pathlib import Path

# Spectral: 0=error, 1=warning, 2=info, 3=hint
SEV_TO_LEVEL = {0: "error", 1: "warning", 2: "note", 3: "note"}
SEV_TO_SCORE = {0: "7.5", 1: "5.0", 2: "2.0", 3: "1.0"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="saída de: spectral lint -f json")
    parser.add_argument("--output", default="api.sarif")
    args = parser.parse_args()

    src = Path(args.input)
    items = json.loads(src.read_text(encoding="utf-8")) if src.exists() else []

    rules, results = {}, []
    for it in items or []:
        code = str(it.get("code", "spectral"))
        rule_id = f"api-{code}"
        sev = int(it.get("severity", 1))
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "shortDescription": {"text": code},
                "properties": {"security-severity": SEV_TO_SCORE.get(sev, "5.0"),
                               "tags": ["api-security"]},
            }
        source = it.get("source", "openapi")
        line = ((it.get("range", {}) or {}).get("start", {}) or {}).get("line", 0)
        results.append({
            "ruleId": rule_id,
            "level": SEV_TO_LEVEL.get(sev, "warning"),
            "message": {"text": str(it.get("message", ""))[:400]},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": str(source).lstrip("/")},
                "region": {"startLine": int(line) + 1},
            }}],
        })

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "Spectral API", "rules": list(rules.values())}},
            "results": results,
        }],
    }
    Path(args.output).write_text(json.dumps(sarif, indent=2), encoding="utf-8")
    print(f"{len(results)} resultado(s) de API lint convertidos para {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
