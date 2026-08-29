#!/usr/bin/env python3
"""SecPipe Dashboard — backend FastAPI + SQLite.

Recebe os findings.json gerados pelo pipeline (POST /api/ingest), mantém o
histórico por repositório com dedup por fingerprint, e serve a interface web.

Rodar:  uvicorn app:app --host 0.0.0.0 --port 8000   (dentro de dashboard/)
Auth:   se a env SECPIPE_TOKEN estiver definida, o /api/ingest exige o
        header X-API-Key com o mesmo valor.
"""

import os
import subprocess
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH    = Path(os.environ.get("SECPIPE_DB",        Path(__file__).parent / "secpipe.db"))
REPO_ROOT  = Path(os.environ.get("SECPIPE_REPO_ROOT", Path(__file__).parent.parent))
POLICY_PATH= Path(os.environ.get("SECPIPE_POLICY",    REPO_ROOT / "policy" / "policy.yml"))
STATIC     = Path(__file__).parent / "static"
SEVERITIES = ["critical", "high", "medium", "low", "info"]
TRIAGE_STATUSES = {"open", "fixed", "false_positive", "accepted"}

app = FastAPI(title="SecPipe Dashboard")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                branch TEXT,
                commit_sha TEXT,
                created_at TEXT NOT NULL,
                critical INTEGER DEFAULT 0, high INTEGER DEFAULT 0,
                medium INTEGER DEFAULT 0, low INTEGER DEFAULT 0, info INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS findings(
                repo TEXT NOT NULL,
                fid TEXT NOT NULL,
                tool TEXT, rule TEXT, severity TEXT,
                file TEXT, line INTEGER, message TEXT,
                status TEXT DEFAULT 'open',
                first_seen TEXT, last_seen TEXT,
                PRIMARY KEY (repo, fid)
            );
            CREATE INDEX IF NOT EXISTS idx_findings_repo ON findings(repo, status, severity);
            CREATE TABLE IF NOT EXISTS repos(
                name TEXT PRIMARY KEY,
                url TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )


init_db()


class Finding(BaseModel):
    id: str
    tool: str = ""
    rule: str = ""
    severity: str = "info"
    file: str = ""
    line: int = 0
    message: str = ""


class IngestPayload(BaseModel):
    repo: str
    branch: str = ""
    commit: str = ""
    summary: dict[str, int] = {}
    findings: list[Finding] = []


def check_token(x_api_key: str | None) -> None:
    token = os.environ.get("SECPIPE_TOKEN")
    if token and x_api_key != token:
        raise HTTPException(status_code=401, detail="X-API-Key inválido")


@app.post("/api/ingest")
def ingest(payload: IngestPayload, x_api_key: str | None = Header(default=None)):
    check_token(x_api_key)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {sev: payload.summary.get(sev, 0) for sev in SEVERITIES}

    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO repos(name, created_at) VALUES (?,?)", (payload.repo, now)
        )
        conn.execute(
            "INSERT INTO scans(repo, branch, commit_sha, created_at, critical, high, medium, low, info)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (payload.repo, payload.branch, payload.commit, now, *[counts[s] for s in SEVERITIES]),
        )
        seen_ids = set()
        for f in payload.findings:
            seen_ids.add(f.id)
            existing = conn.execute(
                "SELECT status FROM findings WHERE repo=? AND fid=?", (payload.repo, f.id)
            ).fetchone()
            if existing:
                # 'fixed' que voltou a aparecer reabre; triagem manual (fp/accepted) é preservada
                new_status = "open" if existing["status"] == "fixed" else existing["status"]
                conn.execute(
                    "UPDATE findings SET tool=?, rule=?, severity=?, file=?, line=?, message=?,"
                    " status=?, last_seen=? WHERE repo=? AND fid=?",
                    (f.tool, f.rule, f.severity, f.file, f.line, f.message,
                     new_status, now, payload.repo, f.id),
                )
            else:
                conn.execute(
                    "INSERT INTO findings(repo, fid, tool, rule, severity, file, line, message,"
                    " status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,'open',?,?)",
                    (payload.repo, f.id, f.tool, f.rule, f.severity, f.file, f.line, f.message, now, now),
                )
        # o que estava aberto e não veio neste scan foi corrigido
        open_rows = conn.execute(
            "SELECT fid FROM findings WHERE repo=? AND status='open'", (payload.repo,)
        ).fetchall()
        fixed = [r["fid"] for r in open_rows if r["fid"] not in seen_ids]
        for fid in fixed:
            conn.execute(
                "UPDATE findings SET status='fixed', last_seen=? WHERE repo=? AND fid=?",
                (now, payload.repo, fid),
            )

    return {"ok": True, "ingested": len(payload.findings), "auto_fixed": len(fixed)}


@app.get("/api/overview")
def overview():
    with db() as conn:
        repos = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM repos UNION SELECT DISTINCT repo FROM scans ORDER BY 1"
            )
        ]
        totals = dict(
            conn.execute(
                "SELECT severity, COUNT(*) FROM findings WHERE status='open' GROUP BY severity"
            ).fetchall()
        )
        by_status = dict(
            conn.execute("SELECT status, COUNT(*) FROM findings GROUP BY status").fetchall()
        )
        last_scans = [
            dict(r)
            for r in conn.execute(
                "SELECT repo, MAX(created_at) AS last_scan, critical, high, medium, low"
                " FROM scans GROUP BY repo ORDER BY repo"
            )
        ]
    return {
        "repos": repos,
        "open_by_severity": {sev: totals.get(sev, 0) for sev in SEVERITIES},
        "by_status": by_status,
        "last_scans": last_scans,
    }


class RepoCreate(BaseModel):
    name: str
    url: str = ""


@app.get("/api/repos")
def list_repos():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT r.name, r.url, r.created_at,
                   (SELECT MAX(created_at) FROM scans s WHERE s.repo = r.name) AS last_scan,
                   (SELECT COUNT(*) FROM scans s WHERE s.repo = r.name) AS scan_count,
                   (SELECT COUNT(*) FROM findings f
                     WHERE f.repo = r.name AND f.status = 'open') AS open_findings,
                   (SELECT COUNT(*) FROM findings f
                     WHERE f.repo = r.name AND f.status = 'open'
                       AND f.severity IN ('critical', 'high')) AS open_critical_high
            FROM (SELECT name, url, created_at FROM repos
                  UNION
                  SELECT DISTINCT repo, '', '' FROM scans
                   WHERE repo NOT IN (SELECT name FROM repos)) r
            ORDER BY r.name
            """
        ).fetchall()
    return {"repos": [dict(r) for r in rows]}


@app.post("/api/repos", status_code=201)
def create_repo(repo: RepoCreate):
    name = repo.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="nome do repositório é obrigatório")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO repos(name, url, created_at) VALUES (?,?,?)",
                (name, repo.url.strip(), now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="repositório já cadastrado")
    return {"ok": True, "name": name}


@app.delete("/api/repos/{name:path}")
def delete_repo(name: str):
    with db() as conn:
        cur = conn.execute("DELETE FROM repos WHERE name=?", (name,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="repositório não encontrado")
    return {"ok": True}


@app.get("/api/scans")
def list_scans(repo: str | None = None, limit: int = 200):
    query = "SELECT id, repo, branch, commit_sha, created_at, critical, high, medium, low, info FROM scans"
    params: list = []
    if repo:
        query += " WHERE repo=?"
        params.append(repo)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    return {"count": len(rows), "scans": rows}


@app.get("/api/trend")
def trend(repo: str | None = None, limit: int = 30):
    query = "SELECT repo, created_at, critical, high, medium, low FROM scans"
    params: list = []
    if repo:
        query += " WHERE repo=?"
        params.append(repo)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    return {"scans": list(reversed(rows))}


@app.get("/api/findings")
def list_findings(
    repo: str | None = None,
    severity: str | None = None,
    status: str | None = "open",
    tool: str | None = None,
    limit: int = 500,
):
    query = "SELECT * FROM findings WHERE 1=1"
    params: list = []
    for column, value in [("repo", repo), ("severity", severity), ("status", status)]:
        if value:
            query += f" AND {column}=?"
            params.append(value)
    if tool:
        query += " AND tool LIKE ?"
        params.append(f"%{tool}%")
    query += (
        " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, last_seen DESC LIMIT ?"
    )
    params.append(limit)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    return {"count": len(rows), "findings": rows}


class TriageUpdate(BaseModel):
    status: str


@app.patch("/api/findings/{repo:path}/{fid}")
def triage(repo: str, fid: str, update: TriageUpdate):
    if update.status not in TRIAGE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status deve ser um de {sorted(TRIAGE_STATUSES)}")
    with db() as conn:
        cur = conn.execute(
            "UPDATE findings SET status=? WHERE repo=? AND fid=?", (update.status, repo, fid)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="finding não encontrado")
    return {"ok": True}


@app.get("/api/runs")
def list_runs(repo: str | None = None, limit: int = 30):
    """Consulta runs do GitHub Actions em tempo real via API pública."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with db() as conn:
        if repo:
            repo_list = [repo]
        else:
            rows = conn.execute(
                "SELECT name FROM repos UNION SELECT DISTINCT repo FROM scans ORDER BY 1"
            ).fetchall()
            repo_list = [r["name"] for r in rows]

    runs = []
    for r in repo_list[:20]:
        try:
            url = f"https://api.github.com/repos/{r}/actions/workflows/security.yml/runs?per_page=3"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                import json as _json
                data = _json.loads(resp.read())
                for run in data.get("workflow_runs", []):
                    runs.append({
                        "repo": r,
                        "run_id": run["id"],
                        "status": run["status"],
                        "conclusion": run.get("conclusion"),
                        "branch": run["head_branch"],
                        "commit": run["head_sha"][:7],
                        "created_at": run["created_at"],
                        "updated_at": run["updated_at"],
                        "url": run["html_url"],
                    })
        except (urllib.error.URLError, Exception):
            pass

    runs.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"runs": runs[:limit]}


def _read_policy() -> dict:
    policy = {"max": {"critical": 0, "high": 0, "medium": 10, "low": 50}, "allowlist": []}
    if not POLICY_PATH.exists():
        return policy
    section = None
    for raw in POLICY_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":"):
            section = stripped[:-1]
        elif section == "max" and ":" in stripped:
            k, _, v = stripped.partition(":")
            try:
                policy["max"][k.strip()] = int(v.strip())
            except ValueError:
                pass
        elif section == "allowlist" and stripped.startswith("- "):
            policy["allowlist"].append(stripped[2:].strip().strip("'\""))
    return policy


def _write_policy(policy: dict) -> None:
    lines = [
        "# Política do gate de segurança — editada via dashboard SecPipe\n",
        "max:\n",
    ]
    for sev in ["critical", "high", "medium", "low"]:
        lines.append(f"  {sev}: {policy['max'].get(sev, 0)}\n")
    lines.append("\nallowlist:\n")
    for entry in policy.get("allowlist", []):
        lines.append(f'  - "{entry}"\n')
    POLICY_PATH.write_text("".join(lines), encoding="utf-8")


class PolicyMax(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 10
    low: int = 50


class PolicyPayload(BaseModel):
    max: PolicyMax
    allowlist: list[str] = []


@app.get("/api/policy")
def get_policy():
    return _read_policy()


@app.put("/api/policy")
def update_policy(payload: PolicyPayload):
    data = {
        "max": payload.max.model_dump(),
        "allowlist": payload.allowlist,
    }
    _write_policy(data)
    return {"ok": True}


@app.post("/api/policy/push")
def push_policy():
    try:
        cwd = str(REPO_ROOT)
        subprocess.run(["git", "add", "policy/policy.yml"], cwd=cwd, check=True)
        subprocess.run(
            ["git", "commit", "-m", "policy: update via dashboard"],
            cwd=cwd, check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=cwd, check=True, capture_output=True)
        return {"ok": True, "message": "Committed and pushed"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.stderr.decode() if e.stderr else str(e))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
