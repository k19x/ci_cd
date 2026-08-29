#!/usr/bin/env python3
"""SecPipe Dashboard — backend FastAPI + SQLite.

Recebe os findings.json gerados pelo pipeline (POST /api/ingest), mantém o
histórico por repositório com dedup por fingerprint, e serve a interface web.

Rodar:  uvicorn app:app --host 0.0.0.0 --port 8000   (dentro de dashboard/)
Auth:   se a env SECPIPE_TOKEN estiver definida, o /api/ingest exige o
        header X-API-Key com o mesmo valor.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH = Path(os.environ.get("SECPIPE_DB", Path(__file__).parent / "secpipe.db"))
STATIC = Path(__file__).parent / "static"
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
        repos = [r["repo"] for r in conn.execute("SELECT DISTINCT repo FROM scans ORDER BY repo")]
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
