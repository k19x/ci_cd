#!/usr/bin/env python3
"""Posta (ou atualiza) o comentário do SecPipe num Pull Request com o resumo do scan.

Só usa stdlib. Requer env GITHUB_TOKEN.

Uso: python pr_comment.py findings.json --repo org/repo --pr 42 --gate success|failure
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- secpipe-report -->"
SEV_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


def gh(url: str, token: str, method: str = "GET", payload: dict | None = None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def build_body(data: dict, gate: str, gate_log: str) -> str:
    summary = data.get("summary", {})
    findings = data.get("findings", [])
    verdict = "✅ **Gate aprovado**" if gate == "success" else "❌ **Gate reprovado**"

    lines = [MARKER, f"## 🛡️ SecPipe Security Scan", "", verdict, ""]
    lines.append("| Severidade | Abertos |")
    lines.append("|---|---|")
    for sev in ("critical", "high", "medium", "low", "info"):
        n = summary.get(sev, 0)
        if n:
            lines.append(f"| {SEV_EMOJI[sev]} {sev} | **{n}** |")
    if not findings:
        lines.append("| — | nenhum finding 🎉 |")
    lines.append("")

    top = [f for f in findings if f.get("severity") in ("critical", "high")][:10]
    if top:
        lines.append("<details><summary><strong>Top findings (critical/high)</strong></summary>")
        lines.append("")
        lines.append("| Sev | Regra | Local | OWASP |")
        lines.append("|---|---|---|---|")
        for f in top:
            loc = f"`{f.get('file','')}:{f.get('line',0)}`"
            lines.append(f"| {SEV_EMOJI.get(f.get('severity'),'')} {f.get('severity')} "
                         f"| `{f.get('rule','')[:60]}` | {loc} | {f.get('owasp','—')} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if gate == "failure" and gate_log:
        tail = "\n".join(gate_log.splitlines()[-15:])
        lines.append("<details><summary>Saída do gate</summary>\n\n```\n" + tail + "\n```\n</details>\n")

    lines.append("_Comentário automático do SecPipe — atualizado a cada push._")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--gate", default="success")
    parser.add_argument("--gate-log", default="")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN ausente — pulando PR decoration.", file=sys.stderr)
        return 0

    data = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    gate_log = Path(args.gate_log).read_text(encoding="utf-8", errors="replace") \
        if args.gate_log and Path(args.gate_log).exists() else ""
    body = build_body(data, args.gate, gate_log)

    base = f"https://api.github.com/repos/{args.repo}"
    try:
        comments = gh(f"{base}/issues/{args.pr}/comments?per_page=100", token)
        existing = next((c for c in comments if str(c.get("body", "")).startswith(MARKER)), None)
        if existing:
            gh(f"{base}/issues/comments/{existing['id']}", token, "PATCH", {"body": body})
            print(f"Comentário do PR #{args.pr} atualizado.")
        else:
            gh(f"{base}/issues/{args.pr}/comments", token, "POST", {"body": body})
            print(f"Comentário criado no PR #{args.pr}.")
    except urllib.error.HTTPError as e:
        # 403 = caller sem pull-requests: write — não falha o pipeline por isso
        print(f"PR decoration falhou (HTTP {e.code}): {e.read().decode(errors='replace')[:150]}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
