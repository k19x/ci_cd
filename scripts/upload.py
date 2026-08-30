#!/usr/bin/env python3
"""Envia o findings.json do pipeline para o SecPipe Dashboard (POST /api/ingest).

Só usa stdlib — nenhuma dependência para instalar no runner.

Uso:
  python upload.py findings.json --url http://dashboard:8000 \
      --repo org/meu-repo --branch main --commit abc123 [--token $SECPIPE_TOKEN]
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings", help="findings.json gerado pelo normalize.py")
    parser.add_argument("--url", required=True, help="URL base do dashboard")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--token", default="", help="valor de SECPIPE_TOKEN, se o dashboard exigir")
    args = parser.parse_args()

    data = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    payload = {
        "repo": args.repo,
        "branch": args.branch,
        "commit": args.commit,
        "summary": data.get("summary", {}),
        "findings": data.get("findings", []),
    }

    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/ingest",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **({"X-API-Key": args.token} if args.token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"ERRO HTTP {exc.code} ao enviar para o dashboard: {exc.reason}", file=sys.stderr)
        try:
            print(f"Body: {exc.read().decode()}", file=sys.stderr)
        except Exception:
            pass
        return 1
    except urllib.error.URLError as exc:
        print(f"ERRO ao enviar para o dashboard: {exc}", file=sys.stderr)
        return 1

    print(f"Dashboard: {body.get('ingested', 0)} finding(s) enviados, "
          f"{body.get('auto_fixed', 0)} marcados como corrigidos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
