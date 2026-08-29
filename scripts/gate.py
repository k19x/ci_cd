#!/usr/bin/env python3
"""Policy gate: decide se o pipeline passa ou falha com base em findings.json.

Regras vêm de policy/policy.yml (limites por severidade + allowlist de
falsos positivos). Exit code 1 = bloqueia o merge.

Uso: python gate.py findings.json --policy policy/policy.yml [--fail-on high]
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]


def load_policy(path: Path) -> dict:
    """Lê o subconjunto de YAML usado em policy.yml sem depender de PyYAML."""
    policy = {"max": {}, "allowlist": []}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":"):
            section = stripped[:-1]
        elif section == "max" and ":" in stripped:
            key, _, value = stripped.partition(":")
            policy["max"][key.strip()] = int(value.strip())
        elif section == "allowlist" and stripped.startswith("- "):
            policy["allowlist"].append(stripped[2:].strip().strip("'\""))
    return policy


def is_allowlisted(finding: dict, allowlist: list[str]) -> bool:
    """Entrada da allowlist casa com o id do finding, o rule id ou um glob de arquivo."""
    for entry in allowlist:
        if entry == finding["id"] or entry == finding["rule"]:
            return True
        if finding["file"] and fnmatch.fnmatch(finding["file"], entry):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings", help="findings.json gerado pelo normalize.py")
    parser.add_argument("--policy", default="policy/policy.yml")
    parser.add_argument(
        "--fail-on",
        choices=SEVERITIES[:-1],
        default=None,
        help="atalho: falha se existir QUALQUER finding desta severidade ou pior (sobrepõe os limites do policy.yml)",
    )
    args = parser.parse_args()

    data = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    policy = load_policy(Path(args.policy))

    active = [f for f in data["findings"] if not is_allowlisted(f, policy["allowlist"])]
    suppressed = data["count"] - len(active)

    counts = {sev: sum(1 for f in active if f["severity"] == sev) for sev in SEVERITIES}
    print(f"Findings ativos: {len(active)} (allowlisted: {suppressed})")
    for sev in SEVERITIES:
        if counts[sev]:
            print(f"  {sev:>8}: {counts[sev]}")

    violations: list[str] = []
    if args.fail_on:
        threshold = SEVERITIES.index(args.fail_on)
        blocking = [f for f in active if SEVERITIES.index(f["severity"]) <= threshold]
        if blocking:
            violations.append(f"{len(blocking)} finding(s) com severidade >= {args.fail_on}")
            worst = blocking[:10]
        else:
            worst = []
    else:
        worst = []
        for sev, limit in policy["max"].items():
            if counts.get(sev, 0) > limit:
                violations.append(f"{sev}: {counts[sev]} encontrados, limite é {limit}")
        blocking_sevs = {s for s in policy["max"] if counts.get(s, 0) > policy["max"][s]}
        worst = [f for f in active if f["severity"] in blocking_sevs][:10]

    if violations:
        print("\n❌ GATE REPROVADO:")
        for v in violations:
            print(f"  - {v}")
        print("\nPrincipais findings bloqueantes:")
        for f in worst:
            print(f"  [{f['severity'].upper()}] {f['tool']} {f['rule']} — {f['file']}:{f['line']}")
            print(f"      {f['message'][:120]}")
        print("\nPara suprimir um falso positivo, adicione o id/rule/arquivo em policy/policy.yml (allowlist).")
        return 1

    print("\n✅ GATE APROVADO — dentro dos limites da política.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
