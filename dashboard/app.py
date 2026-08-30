#!/usr/bin/env python3
"""SecPipe Dashboard — backend FastAPI + SQLite.

Recebe os findings.json gerados pelo pipeline (POST /api/ingest), mantém o
histórico por repositório com dedup por fingerprint, e serve a interface web.

Rodar:  uvicorn app:app --host 0.0.0.0 --port 8000   (dentro de dashboard/)
Auth:   se a env SECPIPE_TOKEN estiver definida, o /api/ingest exige o
        header X-API-Key com o mesmo valor.
"""

import base64
import concurrent.futures
import shutil
import subprocess
import tempfile
import contextlib
import json as _json
import os
import secrets
import sqlite3
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import hashlib
import pyotp

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

DB_PATH     = Path(os.environ.get("SECPIPE_DB",     Path(__file__).parent / "secpipe.db"))
POLICY_PATH = Path(os.environ.get("SECPIPE_POLICY", Path(__file__).parent.parent / "policy" / "policy.yml"))
STATIC      = Path(__file__).parent / "static"
SEVERITIES      = ["critical", "high", "medium", "low", "info"]
TRIAGE_STATUSES = {"open", "fixed", "false_positive", "accepted"}
ROLES           = ["viewer", "analyst", "admin"]

# Auth config
SESSION_COOKIE = "secpipe_session"
SESSION_TTL_H  = int(os.environ.get("SECPIPE_SESSION_TTL", "8"))
SECURE_COOKIE  = os.environ.get("SECPIPE_SECURE_COOKIE", "false").lower() == "true"
_rl: dict      = {}   # rate limiting: ip -> [fail_count, lockout_ts]
RL_MAX, RL_WIN = 10, 900  # 10 attempts → 15 min lockout
_PBKDF2_ITERS  = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256

# Partial sessions: after password OK, before TOTP verified
# token -> {user_id, expires (unix ts)}
_partial: dict = {}
PARTIAL_TTL    = 300  # 5 minutes to enter TOTP code


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key  = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERS)
    return f"pbkdf2$sha256${_PBKDF2_ITERS}${salt}${key.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, algo, iters, salt, hx = stored.split("$")
        key = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iters))
        return secrets.compare_digest(key.hex(), hx)
    except Exception:
        return False

def _set_repo_variable(repo: str, name: str, value: str, headers: dict) -> bool:
    payload = _json.dumps({"name": name, "value": value}).encode()
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/variables/{name}",
            data=payload, method="PATCH", headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{repo}/actions/variables",
                    data=payload, method="POST", headers=headers,
                )
                with urllib.request.urlopen(req, timeout=10):
                    pass
                return True
            except Exception:
                return False
        return False
    except Exception:
        return False


def _auto_register_tunnel():
    """Background: lê a URL do quick tunnel e atualiza SECPIPE_DASHBOARD_URL
    no repo central E em todos os repos cadastrados (o `vars.` do workflow
    reutilizável é resolvido no contexto do repo CALLER, não do ci_cd)."""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    gh_repo  = os.environ.get("SECPIPE_GITHUB_REPO", "k19x/ci_cd")
    if not gh_token:
        return
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    for _ in range(24):  # retry for up to 2 minutes
        time.sleep(5)
        try:
            with urllib.request.urlopen("http://cloudflared:20241/quicktunnel", timeout=3) as r:
                url = _json.loads(r.read()).get("url", "")
            if not url:
                continue
            targets = {gh_repo}
            try:
                with db() as conn:
                    targets |= {row["name"] for row in conn.execute(
                        "SELECT name FROM repos UNION SELECT DISTINCT repo FROM scans")}
            except Exception:
                pass
            ok = 0
            for repo in sorted(targets):
                if "/" in repo and _set_repo_variable(repo, "SECPIPE_DASHBOARD_URL", url, headers):
                    ok += 1
            print(f"[secpipe] SECPIPE_DASHBOARD_URL → {url} ({ok}/{len(targets)} repos atualizados)",
                  flush=True)
            return
        except Exception:
            pass


@contextlib.asynccontextmanager
async def _lifespan(app):
    threading.Thread(target=_auto_register_tunnel, daemon=True).start()
    yield


app = FastAPI(title="SecPipe Dashboard", lifespan=_lifespan)


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
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )


init_db()

# Add columns if upgrading from older schema
_migrations = [
    "ALTER TABLE repos ADD COLUMN visibility TEXT DEFAULT ''",
    "ALTER TABLE repos ADD COLUMN languages TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')",
    "ALTER TABLE findings ADD COLUMN cwe TEXT DEFAULT ''",
    "ALTER TABLE findings ADD COLUMN owasp TEXT DEFAULT ''",
    "CREATE TABLE IF NOT EXISTS audit_log("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
    " username TEXT NOT NULL, action TEXT NOT NULL,"
    " repo TEXT DEFAULT '', target TEXT DEFAULT '', detail TEXT DEFAULT '')",
]
for _mig in _migrations:
    try:
        with db() as _conn:
            _conn.execute(_mig)
    except Exception:
        pass


def seed_admin() -> None:
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        pwd = os.environ.get("SECPIPE_ADMIN_PASSWORD") or secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)",
            ("admin", _hash_password(pwd), "admin", now),
        )
        if not os.environ.get("SECPIPE_ADMIN_PASSWORD"):
            print(f"\n{'='*44}\nSECPIPE FIRST RUN — admin password: {pwd}\n{'='*44}\n", flush=True)


seed_admin()


# ── Auth dependencies ──────────────────────────────
async def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Não autenticado")
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        row = conn.execute(
            "SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON s.user_id=u.id "
            "WHERE s.token=? AND s.expires_at>? AND u.active=1",
            (token, now),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sessão expirada")
    return dict(row)


def require_role(min_role: str):
    async def dep(user=Depends(get_current_user)):
        if ROLES.index(user["role"]) < ROLES.index(min_role):
            raise HTTPException(403, "Permissão insuficiente")
        return user
    return dep


class Finding(BaseModel):
    id: str
    tool: str = ""
    rule: str = ""
    severity: str = "info"
    file: str = ""
    line: int = 0
    message: str = ""
    cwe: str = ""
    owasp: str = ""


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
        new_crits = []
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
                    " cwe=?, owasp=?, status=?, last_seen=? WHERE repo=? AND fid=?",
                    (f.tool, f.rule, f.severity, f.file, f.line, f.message,
                     f.cwe, f.owasp, new_status, now, payload.repo, f.id),
                )
            else:
                conn.execute(
                    "INSERT INTO findings(repo, fid, tool, rule, severity, file, line, message,"
                    " cwe, owasp, status, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,'open',?,?)",
                    (payload.repo, f.id, f.tool, f.rule, f.severity, f.file, f.line, f.message,
                     f.cwe, f.owasp, now, now),
                )
                if f.severity == "critical":
                    new_crits.append(f)
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

    _send_notifications(payload.repo, new_crits)
    return {"ok": True, "ingested": len(payload.findings), "auto_fixed": len(fixed)}


@app.get("/api/overview")
def overview(user=Depends(require_role("viewer"))):
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
        risk = [
            dict(r)
            for r in conn.execute(
                "SELECT repo,"
                " SUM(CASE WHEN severity IN ('critical','high') THEN 1 ELSE 0 END) AS crit_high,"
                " COUNT(*) AS open_total"
                " FROM findings WHERE status='open' GROUP BY repo"
            )
        ]
        engines = dict(
            conn.execute(
                "SELECT tool, COUNT(*) FROM findings WHERE status='open' GROUP BY tool"
            ).fetchall()
        )
        owasp = dict(
            conn.execute(
                "SELECT CASE WHEN owasp='' OR owasp IS NULL THEN 'Sem categoria' ELSE owasp END,"
                " COUNT(*) FROM findings WHERE status='open' GROUP BY 1"
            ).fetchall()
        )

        # SLA: findings abertos há mais tempo que o limite da severidade
        sla = _sla_config()
        now_dt = datetime.now(timezone.utc)
        sla_breached = 0
        for sev_name, days in sla.items():
            cutoff = (now_dt - timedelta(days=days)).isoformat(timespec="seconds")
            sla_breached += conn.execute(
                "SELECT COUNT(*) FROM findings WHERE status='open' AND severity=?"
                " AND first_seen<? AND first_seen!=''",
                (sev_name, cutoff),
            ).fetchone()[0]

        # Risk score por projeto: peso da severidade × fator de idade × exposição
        weights = {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 0.5, "info": 0.0}
        vis_map = dict(conn.execute("SELECT name, COALESCE(visibility,'') FROM repos").fetchall())
        scores: dict = {}
        for row in conn.execute(
            "SELECT repo, severity, first_seen FROM findings WHERE status='open'"
        ):
            age_factor = 1.0
            try:
                age_days = (now_dt - datetime.fromisoformat(row["first_seen"])).days
                age_factor = 1.0 + min(age_days / 30.0, 2.0)   # até 3× para findings velhos
            except Exception:
                pass
            scores[row["repo"]] = scores.get(row["repo"], 0.0) + \
                weights.get(row["severity"], 0.0) * age_factor
        risk_scores = []
        for name, raw in scores.items():
            if vis_map.get(name) == "public":
                raw *= 1.5   # exposição pública pesa mais
            risk_scores.append({"repo": name, "score": round(raw, 1)})
        risk_scores.sort(key=lambda x: -x["score"])

    return {
        "repos": repos,
        "open_by_severity": {sev: totals.get(sev, 0) for sev in SEVERITIES},
        "by_status": by_status,
        "last_scans": last_scans,
        "risk": risk,
        "engines": engines,
        "owasp": owasp,
        "sla": sla,
        "sla_breached": sla_breached,
        "risk_scores": risk_scores,
    }


class RepoCreate(BaseModel):
    name: str
    url: str = ""


@app.get("/api/repos")
def list_repos(user=Depends(require_role("viewer"))):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT r.name, r.url, r.created_at,
                   r.visibility, r.languages,
                   (SELECT MAX(created_at) FROM scans s WHERE s.repo = r.name) AS last_scan,
                   (SELECT COUNT(*) FROM scans s WHERE s.repo = r.name) AS scan_count,
                   (SELECT COUNT(*) FROM findings f
                     WHERE f.repo = r.name AND f.status = 'open') AS open_findings,
                   (SELECT COUNT(*) FROM findings f
                     WHERE f.repo = r.name AND f.status = 'open'
                       AND f.severity IN ('critical', 'high')) AS open_critical_high
            FROM (SELECT name, url, created_at,
                         COALESCE(visibility,'') AS visibility,
                         COALESCE(languages,'') AS languages
                  FROM repos
                  UNION
                  SELECT DISTINCT repo, '', '', '', '' FROM scans
                   WHERE repo NOT IN (SELECT name FROM repos)) r
            ORDER BY r.name
            """
        ).fetchall()
    return {"repos": [dict(r) for r in rows]}


@app.post("/api/repos", status_code=201)
def create_repo(repo: RepoCreate, user=Depends(require_role("analyst"))):
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
    _audit(user["username"], "repo_add", name)
    return {"ok": True, "name": name}


@app.post("/api/repos/{name:path}/refresh-meta")
def refresh_repo_meta(name: str, user=Depends(require_role("analyst"))):
    """Fetch visibility and language info from GitHub and cache in DB."""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    hdrs = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if gh_token:
        hdrs["Authorization"] = f"Bearer {gh_token}"

    visibility = ""
    languages_str = ""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{name}", headers=hdrs)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
            visibility = "private" if data.get("private") else "public"
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{name}/languages", headers=hdrs)
        with urllib.request.urlopen(req, timeout=8) as resp:
            lang_data = _json.loads(resp.read())
            sorted_langs = sorted(lang_data.items(), key=lambda x: x[1], reverse=True)
            languages_str = ",".join(lname for lname, _ in sorted_langs[:6])
    except Exception:
        pass

    with db() as conn:
        conn.execute(
            "UPDATE repos SET visibility=?, languages=? WHERE name=?",
            (visibility, languages_str, name),
        )
    return {"ok": True, "visibility": visibility, "languages": languages_str}


@app.delete("/api/repos/{name:path}")
def delete_repo(name: str, user=Depends(require_role("admin"))):
    with db() as conn:
        cur = conn.execute("DELETE FROM repos WHERE name=?", (name,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="repositório não encontrado")
    _audit(user["username"], "repo_del", name)
    return {"ok": True}


@app.get("/api/scans")
def list_scans(repo: str | None = None, limit: int = 200, user=Depends(require_role("viewer"))):
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
def trend(repo: str | None = None, limit: int = 30, user=Depends(require_role("viewer"))):
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
    user=Depends(require_role("viewer")),
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
def triage(repo: str, fid: str, update: TriageUpdate, user=Depends(require_role("analyst"))):
    if update.status not in TRIAGE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status deve ser um de {sorted(TRIAGE_STATUSES)}")
    with db() as conn:
        cur = conn.execute(
            "UPDATE findings SET status=? WHERE repo=? AND fid=?", (update.status, repo, fid)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="finding não encontrado")
    _audit(user["username"], "triage", repo, fid, update.status)
    return {"ok": True}


def _dispatch_security_scan(repo: str) -> None:
    """Dispara o workflow security.yml (workflow_dispatch) — main com fallback para master."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN não configurado")
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/security.yml/dispatches"
    try:
        req = urllib.request.Request(url, data=_json.dumps({"ref": "main"}).encode(),
                                     headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 422:
            try:
                req2 = urllib.request.Request(url, data=_json.dumps({"ref": "master"}).encode(),
                                              headers=hdrs, method="POST")
                with urllib.request.urlopen(req2, timeout=10):
                    pass
            except urllib.error.HTTPError as e2:
                raise HTTPException(status_code=e2.code, detail=e2.read().decode(errors="replace"))
        else:
            raise HTTPException(status_code=e.code, detail=body)
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/repos/{repo:path}/dispatch")
def dispatch_scan(repo: str, user=Depends(require_role("analyst"))):
    _dispatch_security_scan(repo)
    _audit(user["username"], "scan_dispatch", repo)
    return {"ok": True, "repo": repo}


@app.post("/api/runs/dispatch-all")
def dispatch_all_scans(user=Depends(require_role("analyst"))):
    """Dispara security.yml em todos os repos cadastrados em paralelo."""
    with db() as conn:
        rows = conn.execute(
            "SELECT name FROM repos UNION SELECT DISTINCT repo FROM scans ORDER BY 1"
        ).fetchall()
    repos = [r["name"] for r in rows]
    if not repos:
        raise HTTPException(status_code=404, detail="Nenhum repo cadastrado")

    results = []

    def _try_dispatch(repo: str) -> dict:
        try:
            _dispatch_security_scan(repo)
            _audit(user["username"], "scan_dispatch", repo)
            return {"repo": repo, "ok": True}
        except Exception as e:
            return {"repo": repo, "ok": False, "error": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(_try_dispatch, repos):
            results.append(res)

    ok_count = sum(1 for r in results if r["ok"])
    return {"dispatched": ok_count, "total": len(repos), "results": results}


@app.get("/api/runs")
def list_runs(repo: str | None = None, limit: int = 30, user=Depends(require_role("viewer"))):
    """Consulta runs do GitHub Actions em paralelo via API."""
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

    def fetch_repo(r: str) -> list:
        try:
            url = f"https://api.github.com/repos/{r}/actions/workflows/security.yml/runs?per_page=3"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
                return [
                    {
                        "repo": r,
                        "run_id": run["id"],
                        "status": run["status"],
                        "conclusion": run.get("conclusion"),
                        "branch": run["head_branch"],
                        "commit": run["head_sha"][:7],
                        "created_at": run["created_at"],
                        "updated_at": run["updated_at"],
                        "url": run["html_url"],
                    }
                    for run in data.get("workflow_runs", [])
                ]
        except Exception:
            return []

    runs: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for result in ex.map(fetch_repo, repo_list[:20]):
            runs.extend(result)

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
def get_policy(user=Depends(require_role("viewer"))):
    return _read_policy()


@app.put("/api/policy")
def update_policy(payload: PolicyPayload, user=Depends(require_role("admin"))):
    data = {
        "max": payload.max.model_dump(),
        "allowlist": payload.allowlist,
    }
    _write_policy(data)
    return {"ok": True}


@app.post("/api/policy/push")
def push_policy(user=Depends(require_role("admin"))):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN não configurado no .env")

    github_repo = os.environ.get("SECPIPE_GITHUB_REPO", "k19x/ci_cd")
    remote_path = "policy/policy.yml"
    content = POLICY_PATH.read_text(encoding="utf-8")
    b64 = base64.b64encode(content.encode()).decode()

    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{github_repo}/contents/{remote_path}",
            headers=hdrs,
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            sha = _json.loads(r.read())["sha"]
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"GitHub API (read): {e}")

    try:
        payload = _json.dumps({
            "message": "policy: update via SecPipe dashboard",
            "content": b64,
            "sha": sha,
        }).encode()
        req2 = urllib.request.Request(
            f"https://api.github.com/repos/{github_repo}/contents/{remote_path}",
            data=payload,
            headers={**hdrs, "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req2, timeout=10) as r:
            commit_sha = _json.loads(r.read())["commit"]["sha"][:7]
        return {"ok": True, "commit": commit_sha}
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"GitHub API (write): {e}")


# ── Auth endpoints ─────────────────────────────────
class LoginPayload(BaseModel):
    username: str
    password: str


def _create_session(user_id: int, username: str, role: str) -> JSONResponse:
    _audit(username, "login")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_H)).isoformat()
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,?)",
            (token, user_id, expires),
        )
    resp = JSONResponse({"ok": True, "username": username, "role": role})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_H * 3600,
                    httponly=True, samesite="lax", secure=SECURE_COOKIE)
    return resp


@app.post("/api/auth/login")
async def login(payload: LoginPayload, request: Request):
    ip = request.client.host if request.client else "unknown"
    ts = time.time()
    rl = _rl.get(ip, [0, 0.0])
    if rl[1] > ts:
        raise HTTPException(429, f"Muitas tentativas. Aguarde {int(rl[1]-ts)}s")
    with db() as conn:
        u = conn.execute(
            "SELECT id,username,password_hash,role,active,totp_enabled,totp_secret "
            "FROM users WHERE username=?",
            (payload.username.strip(),),
        ).fetchone()
    ok = u and u["active"] and _verify_password(payload.password, u["password_hash"])
    if not ok:
        rl[0] += 1
        if rl[0] >= RL_MAX:
            rl[1] = ts + RL_WIN
            rl[0] = 0
        _rl[ip] = rl
        raise HTTPException(401, "Credenciais inválidas")
    _rl.pop(ip, None)

    if u["totp_enabled"] and u["totp_secret"]:
        # Password OK but TOTP required — issue a short-lived partial token
        pt = secrets.token_urlsafe(24)
        _partial[pt] = {"user_id": u["id"], "username": u["username"],
                        "role": u["role"], "exp": ts + PARTIAL_TTL}
        return JSONResponse({"require_totp": True, "partial": pt})

    return _create_session(u["id"], u["username"], u["role"])


class TOTPConfirmPayload(BaseModel):
    partial: str
    code: str


@app.post("/api/auth/totp/confirm")
async def totp_confirm(payload: TOTPConfirmPayload, request: Request):
    entry = _partial.get(payload.partial)
    if not entry or time.time() > entry["exp"]:
        _partial.pop(payload.partial, None)
        raise HTTPException(401, "Sessão expirada. Faça login novamente.")

    ip = request.client.host if request.client else "unknown"
    ts = time.time()
    rl = _rl.get(f"totp:{ip}", [0, 0.0])
    if rl[1] > ts:
        raise HTTPException(429, f"Muitas tentativas. Aguarde {int(rl[1]-ts)}s")

    with db() as conn:
        u = conn.execute(
            "SELECT totp_secret FROM users WHERE id=?", (entry["user_id"],)
        ).fetchone()

    if not u or not pyotp.TOTP(u["totp_secret"]).verify(payload.code.strip(), valid_window=1):
        rl[0] += 1
        if rl[0] >= 5:
            rl[1] = ts + 300  # 5 min lockout on TOTP brute force
            rl[0] = 0
        _rl[f"totp:{ip}"] = rl
        raise HTTPException(401, "Código 2FA inválido")

    _partial.pop(payload.partial, None)
    _rl.pop(f"totp:{ip}", None)
    return _create_session(entry["user_id"], entry["username"], entry["role"])


@app.get("/api/auth/totp/status")
async def totp_status(user=Depends(require_role("viewer"))):
    with db() as conn:
        row = conn.execute(
            "SELECT totp_enabled FROM users WHERE id=?", (user["id"],)
        ).fetchone()
    return {"enabled": bool(row and row["totp_enabled"])}


@app.get("/api/auth/totp/setup")
async def totp_setup(user=Depends(require_role("viewer"))):
    """Generate a new TOTP secret for the current user (not yet activated)."""
    secret = pyotp.random_base32()
    issuer = "SecPipe"
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name=issuer)
    return {"secret": secret, "uri": uri}


class TOTPActivatePayload(BaseModel):
    secret: str
    code: str


@app.post("/api/auth/totp/activate")
async def totp_activate(payload: TOTPActivatePayload, user=Depends(require_role("viewer"))):
    """Verify code against new secret and enable TOTP for the current user."""
    if not pyotp.TOTP(payload.secret).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(400, "Código inválido. Verifique o app autenticador.")
    with db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret=?, totp_enabled=1 WHERE id=?",
            (payload.secret, user["id"]),
        )
    return {"ok": True}


class TOTPDisablePayload(BaseModel):
    password: str


@app.post("/api/auth/totp/disable")
async def totp_disable(payload: TOTPDisablePayload, user=Depends(require_role("viewer"))):
    """Disable TOTP for the current user (requires current password)."""
    with db() as conn:
        u = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()
    if not u or not _verify_password(payload.password, u["password_hash"]):
        raise HTTPException(401, "Senha incorreta")
    with db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret='', totp_enabled=0 WHERE id=?",
            (user["id"],),
        )
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/auth/me")
async def me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Não autenticado")
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        row = conn.execute(
            "SELECT u.username,u.role FROM sessions s JOIN users u ON s.user_id=u.id "
            "WHERE s.token=? AND s.expires_at>? AND u.active=1",
            (token, now),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sessão expirada")
    return {"username": row["username"], "role": row["role"]}


# ── User management (admin only) ───────────────────
class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class PasswordChange(BaseModel):
    password: str


@app.get("/api/users")
def list_users(user=Depends(require_role("admin"))):
    with db() as conn:
        rows = conn.execute(
            "SELECT id,username,role,created_at,active FROM users ORDER BY username"
        ).fetchall()
    return {"users": [dict(r) for r in rows]}


@app.post("/api/users", status_code=201)
def create_user(payload: UserCreate, user=Depends(require_role("admin"))):
    if payload.role not in ROLES:
        raise HTTPException(400, f"Role inválido. Use: {ROLES}")
    if len(payload.password) < 8:
        raise HTTPException(400, "Senha mínima: 8 caracteres")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)",
                (payload.username.strip(), _hash_password(payload.password), payload.role, now),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Usuário já existe")
    return {"ok": True}


@app.delete("/api/users/{username}")
def delete_user(username: str, user=Depends(require_role("admin"))):
    if username == user["username"]:
        raise HTTPException(400, "Não é possível remover a si mesmo")
    with db() as conn:
        cur = conn.execute("DELETE FROM users WHERE username=?", (username,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Usuário não encontrado")
    return {"ok": True}


@app.put("/api/users/{username}/password")
def change_password(username: str, payload: PasswordChange, user=Depends(get_current_user)):
    if user["username"] != username and ROLES.index(user["role"]) < ROLES.index("admin"):
        raise HTTPException(403, "Permissão insuficiente")
    if len(payload.password) < 8:
        raise HTTPException(400, "Senha mínima: 8 caracteres")
    with db() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (_hash_password(payload.password), username),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Usuário não encontrado")
    return {"ok": True}


# ── Re-run workflow ────────────────────────────────
@app.get("/api/runs/{repo:path}/{run_id}/jobs")
def get_run_jobs(repo: str, run_id: int, user=Depends(require_role("viewer"))):
    """Return job-level failure details for a specific workflow run."""
    token = os.environ.get("GITHUB_TOKEN", "")
    hdrs = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"

    def gh_get(url):
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            return _json.loads(r.read())

    try:
        jobs_data = gh_get(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=30")
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except urllib.error.URLError as e:
        raise HTTPException(502, str(e))

    # Fetch run metadata to get branch and event type
    run_meta: dict = {}
    try:
        run_meta = gh_get(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}")
    except Exception:
        pass

    jobs = []
    gate_failed = False
    for job in jobs_data.get("jobs", []):
        failed_steps = [
            {"name": s["name"], "number": s["number"]}
            for s in job.get("steps", [])
            if s.get("conclusion") in ("failure", "timed_out")
        ]
        if job.get("conclusion") == "failure" and any(s["name"] == "Enforce gate" for s in failed_steps):
            gate_failed = True
        jobs.append({
            "name": job["name"],
            "conclusion": job.get("conclusion"),
            "status": job.get("status"),
            "failed_steps": failed_steps,
            "url": job.get("html_url", ""),
            "completed_at": job.get("completed_at", ""),
        })

    gate_context = None
    if gate_failed:
        branch = run_meta.get("head_branch", "")
        event = run_meta.get("event", "push")
        is_pr = event == "pull_request" or (branch and branch != "main" and branch != "master")
        with db() as conn:
            row = conn.execute(
                "SELECT SUM(CASE WHEN severity='critical' AND status='open' THEN 1 ELSE 0 END) as crits,"
                " SUM(CASE WHEN severity='high'     AND status='open' THEN 1 ELSE 0 END) as highs"
                " FROM findings WHERE repo=?",
                (repo,)
            ).fetchone()
        crits = row["crits"] or 0
        highs = row["highs"] or 0
        if is_pr:
            note = (
                f"Esta PR foi bloqueada pelo gate por <strong>{crits} finding(s) crítico(s)</strong> "
                f"já existentes no repositório. "
                f"Trivy e Gitleaks não são diff-aware — eles escaneiam o repo inteiro, "
                f"não apenas as mudanças da PR. Corrija os criticals na branch base para desbloquear."
            )
        else:
            note = (
                f"Gate bloqueado: <strong>{crits} critical(s)</strong> e <strong>{highs} high(s)</strong> "
                f"em aberto no repositório. Corrija os findings ou ajuste a política <code>fail_on</code>."
            )
        gate_context = {"critical_count": crits, "high_count": highs, "is_pr": is_pr, "note": note, "branch": branch}

    return {"jobs": jobs, "gate_context": gate_context}


@app.post("/api/runs/{repo:path}/{run_id}/rerun")
def rerun_scan(repo: str, run_id: int, user=Depends(require_role("analyst"))):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(400, "GITHUB_TOKEN não configurado")
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun",
            data=b"{}",
            headers=hdrs,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except urllib.error.URLError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


# ── AI Engine (Claude) ────────────────────────────────────────────────────
AI_MODELS = ["claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5",
             "claude-fable-5", "claude-haiku-4-5-20251001"]
_ai_cache: dict = {}   # (kind, repo, key) -> analysis text


def _get_setting(key: str, default: str = "") -> str:
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def _set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _ai_model() -> str:
    return _get_setting("ai_model") or os.environ.get("SECPIPE_AI_MODEL", "claude-sonnet-5")


# ── Audit log ─────────────────────────────────────────────────────────────
def _audit(username: str, action: str, repo: str = "", target: str = "", detail: str = "") -> None:
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, username, action, repo, target, detail)"
                " VALUES (?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 username, action, repo, target, str(detail)[:300]),
            )
    except Exception:
        pass


@app.get("/api/audit")
def audit_list(limit: int = 300, user=Depends(require_role("admin"))):
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT ts, username, action, repo, target, detail FROM audit_log"
            " ORDER BY id DESC LIMIT ?", (min(limit, 1000),))]
    return {"count": len(rows), "entries": rows}


# ── SLA ───────────────────────────────────────────────────────────────────
def _sla_config() -> dict:
    base = {"critical": 7, "high": 30, "medium": 90, "low": 180}
    try:
        cfg = _json.loads(_get_setting("sla") or "{}")
        base.update({k: int(v) for k, v in cfg.items() if k in base and int(v) > 0})
    except Exception:
        pass
    return base


class SLAConfig(BaseModel):
    critical: int = 7
    high: int = 30
    medium: int = 90
    low: int = 180


@app.get("/api/sla")
def sla_get(user=Depends(require_role("viewer"))):
    return _sla_config()


@app.put("/api/sla")
def sla_put(cfg: SLAConfig, user=Depends(require_role("admin"))):
    _set_setting("sla", _json.dumps(cfg.model_dump()))
    _audit(user["username"], "sla_change", detail=str(cfg.model_dump()))
    return {"ok": True}


# ── Notificações (Slack / Discord / WhatsApp via CallMeBot) ───────────────
def _send_notifications(repo: str, new_crits: list) -> None:
    if not new_crits:
        return
    rules = sorted({getattr(f, "rule", "") or "?" for f in new_crits})[:5]
    msg = (f"🔴 SecPipe: {len(new_crits)} novo(s) finding(s) CRITICAL em {repo} — "
           + ", ".join(rules))

    def send():
        slack = _get_setting("notify_slack")
        discord = _get_setting("notify_discord")
        wa = _get_setting("notify_whatsapp")
        for url, payload in ((slack, {"text": msg}), (discord, {"content": msg})):
            if url:
                try:
                    req = urllib.request.Request(
                        url, data=_json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                except Exception:
                    pass
        if wa:
            try:
                sep = "&" if "?" in wa else "?"
                with urllib.request.urlopen(wa + sep + "text=" + urllib.parse.quote(msg), timeout=10):
                    pass
            except Exception:
                pass

    threading.Thread(target=send, daemon=True).start()


class NotifyConfig(BaseModel):
    slack: str = ""
    discord: str = ""
    whatsapp: str = ""


@app.get("/api/notify/config")
def notify_get(user=Depends(require_role("admin"))):
    return {"slack": _get_setting("notify_slack"),
            "discord": _get_setting("notify_discord"),
            "whatsapp": _get_setting("notify_whatsapp")}


@app.put("/api/notify/config")
def notify_put(cfg: NotifyConfig, user=Depends(require_role("admin"))):
    for key, val in (("notify_slack", cfg.slack), ("notify_discord", cfg.discord),
                     ("notify_whatsapp", cfg.whatsapp)):
        _set_setting(key, val.strip())
    _audit(user["username"], "notify_change")
    return {"ok": True}


@app.post("/api/notify/test")
def notify_test(user=Depends(require_role("admin"))):
    class _F:
        rule = "mensagem-de-teste"
    _send_notifications("secpipe/teste", [_F()])
    return {"ok": True, "detail": "Teste enviado aos canais configurados (verifique lá)"}
_ai_jobs:  dict = {}   # job_id -> {status: running|done|error, result?, error?, ts}


def _start_ai_job(fn, arg) -> dict:
    """Executa fn(arg) em background — evita o timeout (~100s) do tunnel Cloudflare."""
    cutoff = time.time() - 3600
    for k in [k for k, v in _ai_jobs.items() if v.get("ts", 0) < cutoff]:
        _ai_jobs.pop(k, None)
    job_id = secrets.token_hex(8)
    _ai_jobs[job_id] = {"status": "running", "ts": time.time()}
    def run():
        try:
            _ai_jobs[job_id] = {"status": "done", "result": fn(arg), "ts": time.time()}
        except HTTPException as e:
            _ai_jobs[job_id] = {"status": "error", "error": str(e.detail), "ts": time.time()}
        except Exception as e:
            _ai_jobs[job_id] = {"status": "error", "error": str(e)[:300], "ts": time.time()}
    threading.Thread(target=run, daemon=True).start()
    return {"job": job_id}


@app.get("/api/ai/jobs/{job_id}")
def ai_job_status(job_id: str, user=Depends(require_role("viewer"))):
    job = _ai_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado (expirou ou o serviço reiniciou)")
    return job


def _claude(system: str, prompt: str, max_tokens: int = 1600) -> str:
    """Roteia para API direta (ANTHROPIC_API_KEY) ou Claude Code CLI (CLAUDE_CODE_OAUTH_TOKEN)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _claude_api(system, prompt, max_tokens)
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return _claude_cli(system, prompt)
    raise HTTPException(400, "Configure ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN no .env "
                             "(gere o token com: claude setup-token)")


def _claude_cli(system: str, prompt: str) -> str:
    """Usa o Claude Code CLI em modo headless (-p), autenticado pela conta do usuário."""
    env = dict(os.environ)
    env["HOME"] = "/tmp"          # CLI precisa de home gravável (container é read-only + tmpfs)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "text", "--model", _ai_model()],
            input=f"{system}\n\n{prompt}",
            capture_output=True, text=True, timeout=180, env=env, cwd="/tmp",
        )
    except FileNotFoundError:
        raise HTTPException(500, "Claude Code CLI não está na imagem — rode: docker compose up --build -d")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Claude Code excedeu o tempo limite (180s)")
    if proc.returncode != 0:
        raise HTTPException(502, f"Claude Code: {(proc.stderr or proc.stdout or 'erro desconhecido')[:300]}")
    out = proc.stdout.strip()
    if not out:
        raise HTTPException(502, "Claude Code retornou resposta vazia")
    return out


def _claude_api(system: str, prompt: str, max_tokens: int = 1600) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    body = _json.dumps({
        "model": _ai_model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = _json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"Claude API {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise HTTPException(502, f"Claude API inacessível: {e}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


@app.get("/api/ai/status")
def ai_status(user=Depends(require_role("viewer"))):
    via = ("api" if os.environ.get("ANTHROPIC_API_KEY")
           else "claude-code" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else None)
    return {"enabled": via is not None, "via": via, "model": _ai_model()}


class AIConfig(BaseModel):
    model: str


@app.get("/api/ai/config")
def ai_config_get(user=Depends(require_role("viewer"))):
    via = ("api" if os.environ.get("ANTHROPIC_API_KEY")
           else "claude-code" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else None)
    return {"model": _ai_model(), "models": AI_MODELS, "enabled": via is not None, "via": via}


@app.put("/api/ai/config")
def ai_config_put(cfg: AIConfig, user=Depends(require_role("admin"))):
    m = cfg.model.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not m or len(m) > 80 or any(c not in allowed for c in m):
        raise HTTPException(400, "ID de modelo inválido")
    _set_setting("ai_model", m)
    _audit(user["username"], "ai_model_change", detail=m)
    return {"ok": True, "model": m}


class AIFixRequest(BaseModel):
    repo: str
    fid: str = ""
    rule: str = ""
    severity: str = ""
    file: str = ""
    line: int = 0
    message: str = ""
    tool: str = ""


def _do_fix(r: AIFixRequest):
    """Gera sugestão de correção para um finding, com o código real como contexto."""
    ck = ("fix", r.repo, r.fid or f"{r.file}:{r.line}:{r.rule}")
    if ck in _ai_cache:
        return {"analysis": _ai_cache[ck], "cached": True}

    # busca o trecho de código no GitHub (±25 linhas ao redor do finding)
    snippet = ""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if r.file and gh_token:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{r.repo}/contents/{r.file}?ref=main",
                headers={
                    "Authorization": f"Bearer {gh_token}",
                    "Accept": "application/vnd.github.raw+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", "replace")
            lines = content.splitlines()
            lo = max(0, (r.line or 1) - 26)
            hi = min(len(lines), (r.line or 1) + 25)
            snippet = "\n".join(f"{n}: {l}" for n, l in enumerate(lines[lo:hi], start=lo + 1))
        except Exception:
            pass

    snippet_block = f"Trecho do código (linha: conteúdo):\n```\n{snippet}\n```" if snippet \
        else "(código-fonte indisponível — analise pela regra e mensagem)"
    prompt = (
        f"Finding de segurança detectado pelo SecPipe:\n"
        f"- Repositório: {r.repo}\n"
        f"- Engine: {r.tool}\n"
        f"- Regra: {r.rule}\n"
        f"- Severidade: {r.severity}\n"
        f"- Arquivo: {r.file} (linha {r.line})\n"
        f"- Mensagem: {r.message}\n\n"
        f"{snippet_block}\n\n"
        "Responda em português, direto ao ponto:\n"
        "1. **Causa** — por que isso é vulnerável (2-3 frases)\n"
        "2. **Correção** — o código corrigido em um bloco ```\n"
        "3. **Verificar também** — riscos relacionados no mesmo padrão\n"
        "Seja específico ao código mostrado; não invente contexto que não existe."
    )
    system = ("Você é um engenheiro de segurança de aplicações sênior fazendo triagem "
              "de findings de SAST/SCA/secrets. Responda em português brasileiro com markdown simples.")
    analysis = _claude(system, prompt)
    _ai_cache[ck] = analysis
    return {"analysis": analysis, "cached": False}


def _run_git(args: list, cwd: str, timeout: int = 60) -> str:
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "")[:300]
        tok = os.environ.get("GITHUB_TOKEN", "")
        if tok:
            msg = msg.replace(tok, "***")
        raise HTTPException(502, f"git {args[0]}: {msg}")
    return proc.stdout


def _do_autofix(r: AIFixRequest):
    """Clona o repo, deixa o Claude Code corrigir o finding e abre um Pull Request."""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        raise HTTPException(400, "GITHUB_TOKEN não configurado")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        raise HTTPException(400, "Configure ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN no .env")
    if not r.file:
        raise HTTPException(400, "Finding sem arquivo associado — correção automática indisponível")

    workdir = tempfile.mkdtemp(prefix="secpipe-fix-", dir="/tmp")
    try:
        clone_url = f"https://x-access-token:{gh_token}@github.com/{r.repo}.git"
        _run_git(["clone", "--depth", "1", clone_url, workdir], cwd="/tmp", timeout=120)
        branch = f"secpipe/ai-fix-{int(time.time())}"
        _run_git(["checkout", "-b", branch], cwd=workdir)

        prompt = (
            f"Você está no repositório {r.repo}. Corrija este finding de segurança:\n"
            f"- Engine: {r.tool}\n- Regra: {r.rule}\n- Severidade: {r.severity}\n"
            f"- Arquivo: {r.file} (linha {r.line})\n- Mensagem: {r.message}\n\n"
            "Instruções:\n"
            "1. Leia o arquivo apontado e entenda o contexto\n"
            "2. Aplique a correção mínima e segura — não refatore nada além do necessário\n"
            "3. Se a correção exigir mudanças em outros pontos do MESMO padrão vulnerável no arquivo, corrija também\n"
            "4. NÃO altere arquivos não relacionados, não adicione dependências, não crie arquivos novos\n"
            "5. Ao final, resuma em 2-3 frases o que mudou"
        )
        env = dict(os.environ)
        env["HOME"] = "/tmp"
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "text", "--model", _ai_model(),
             "--allowedTools", "Edit,Write,Read,Grep,Glob", "--max-turns", "25"],
            input=prompt, capture_output=True, text=True, timeout=300, env=env, cwd=workdir,
        )
        if proc.returncode != 0:
            raise HTTPException(502, f"Claude Code: {(proc.stderr or proc.stdout or '')[:300]}")
        summary = proc.stdout.strip()[:2000]

        if not _run_git(["status", "--porcelain"], cwd=workdir).strip():
            return {"applied": False, "detail": "A IA analisou mas não fez alterações no código.",
                    "analysis": summary}

        diff = _run_git(["diff"], cwd=workdir)[:8000]
        _run_git(["add", "-A"], cwd=workdir)
        _run_git(["-c", "user.name=SecPipe AI", "-c", "user.email=secpipe-ai@users.noreply.github.com",
                  "commit", "-m",
                  f"fix(security): {r.rule or 'finding'} em {r.file}\n\n"
                  f"Correção automática gerada pelo SecPipe AI Engine (Claude).\n"
                  f"Finding: {r.tool} / {r.severity} / linha {r.line}"],
                 cwd=workdir)
        _run_git(["push", "origin", branch], cwd=workdir, timeout=90)

        # branch padrão do repo (para o base do PR)
        hdrs = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}
        base = "main"
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{r.repo}", headers=hdrs)
            with urllib.request.urlopen(req, timeout=10) as resp:
                base = _json.loads(resp.read()).get("default_branch", "main")
        except Exception:
            pass

        pr_payload = _json.dumps({
            "title": f"[SecPipe AI] fix: {r.rule or r.file}",
            "head": branch, "base": base,
            "body": (f"## 🤖 Correção automática — SecPipe AI Engine\n\n"
                     f"| | |\n|---|---|\n| **Engine** | {r.tool} |\n| **Regra** | `{r.rule}` |\n"
                     f"| **Severidade** | {r.severity} |\n| **Arquivo** | `{r.file}:{r.line}` |\n\n"
                     f"**Mensagem do scanner:** {r.message}\n\n"
                     f"**Resumo da IA:**\n{summary}\n\n"
                     f"> ⚠️ Correção gerada por IA — revise antes do merge. "
                     f"O scan de segurança rodará automaticamente neste PR."),
        }).encode()
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{r.repo}/pulls",
                                         data=pr_payload, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                pr = _json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise HTTPException(e.code, f"Branch enviado ({branch}) mas o PR falhou: "
                                        f"{e.read().decode(errors='replace')[:200]}")

        return {"applied": True, "pr_url": pr.get("html_url", ""), "branch": branch,
                "diff": diff, "summary": summary}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Correção automática excedeu o tempo limite")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class AIDiagnoseRequest(BaseModel):
    repo: str
    run_id: int


def _do_diagnose(r: AIDiagnoseRequest):
    """Diagnostica a causa de um scan que falhou, lendo jobs + logs do GitHub Actions."""
    ck = ("diag", r.repo, str(r.run_id))
    if ck in _ai_cache:
        return {"analysis": _ai_cache[ck], "cached": True}

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        raise HTTPException(400, "GITHUB_TOKEN não configurado")
    hdrs = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{r.repo}/actions/runs/{r.run_id}/jobs?per_page=50",
            headers=hdrs,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            jobs_data = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace")[:200])

    summary, logs_excerpt = [], ""
    for job in jobs_data.get("jobs", []):
        concl = job.get("conclusion") or job.get("status")
        summary.append(f"- Job '{job['name']}': {concl}")
        for s in job.get("steps", []):
            if s.get("conclusion") in ("failure", "timed_out"):
                summary.append(f"    step FALHOU: '{s['name']}'")
        # pega o log do primeiro job que falhou (últimas 150 linhas)
        if concl in ("failure", "timed_out") and not logs_excerpt:
            try:
                lreq = urllib.request.Request(
                    f"https://api.github.com/repos/{r.repo}/actions/jobs/{job['id']}/logs",
                    headers=hdrs,
                )
                with urllib.request.urlopen(lreq, timeout=15) as lresp:
                    text = lresp.read().decode("utf-8", "replace")
                logs_excerpt = "\n".join(text.splitlines()[-150:])[-6000:]
            except Exception:
                pass

    prompt = (
        f"Scan de segurança (GitHub Actions) falhou no repositório {r.repo} (run {r.run_id}).\n\n"
        f"Resumo dos jobs:\n" + "\n".join(summary) + "\n\n"
        + (f"Últimas linhas do log do job que falhou:\n```\n{logs_excerpt}\n```\n\n" if logs_excerpt else "")
        + "O pipeline tem 4 jobs: SAST (Semgrep), SCA+IaC+Secrets (Trivy), Secrets histórico (Gitleaks) "
          "e Policy Gate (normaliza SARIFs, envia ao dashboard SecPipe e aplica gate de severidade — "
          "exit 1 = findings acima do limite da política).\n\n"
        "Responda em português:\n"
        "1. **Causa provável** — o que quebrou (seja específico: erro de infra, gate reprovado, config, etc.)\n"
        "2. **Como corrigir** — passos concretos\n"
        "3. **É bloqueio do gate ou erro técnico?** — uma frase"
    )
    system = ("Você é um engenheiro de CI/CD e DevSecOps diagnosticando falhas de pipeline. "
              "Responda em português brasileiro com markdown simples.")
    analysis = _claude(system, prompt)
    _ai_cache[ck] = analysis
    return {"analysis": analysis, "cached": False}


class AIVerifyRequest(BaseModel):
    repo: str
    fid: str


def _do_verify(r: AIVerifyRequest):
    """Dispara novo scan, espera o resultado ser ingerido e confere se o finding sumiu.

    O ingest já marca como 'fixed' automaticamente todo finding aberto que não
    aparece no novo upload — aqui só orquestramos e reportamos o desfecho.
    """
    start_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _dispatch_security_scan(r.repo)

    deadline = time.time() + 900   # 15 min
    while time.time() < deadline:
        time.sleep(20)
        with db() as conn:
            scan = conn.execute(
                "SELECT created_at FROM scans WHERE repo=? AND created_at>? "
                "ORDER BY created_at DESC LIMIT 1",
                (r.repo, start_iso),
            ).fetchone()
        if not scan:
            continue
        with db() as conn:
            f = conn.execute(
                "SELECT status FROM findings WHERE repo=? AND fid=?",
                (r.repo, r.fid),
            ).fetchone()
        status = f["status"] if f else "removed"
        fixed = (f is None) or (f["status"] == "fixed")
        return {"verified": True, "fixed": fixed, "status": status,
                "scan_at": scan["created_at"]}
    return {"verified": False,
            "detail": "O novo scan não foi ingerido em 15 min — confira a aba Scans "
                      "(o run pode ter falhado ou ainda estar em execução)."}


@app.post("/api/ai/verify")
def ai_verify(r: AIVerifyRequest, user=Depends(require_role("analyst"))):
    return _start_ai_job(_do_verify, r)


@app.post("/api/ai/fix")
def ai_fix(r: AIFixRequest, user=Depends(require_role("analyst"))):
    return _start_ai_job(_do_fix, r)


@app.post("/api/ai/autofix")
def ai_autofix(r: AIFixRequest, user=Depends(require_role("analyst"))):
    _audit(user["username"], "ai_autofix", r.repo, r.fid or r.file, r.rule)
    return _start_ai_job(_do_autofix, r)


@app.post("/api/ai/diagnose")
def ai_diagnose(r: AIDiagnoseRequest, user=Depends(require_role("analyst"))):
    return _start_ai_job(_do_diagnose, r)


# ── Relatório executivo ───────────────────────────────────────────────────
@app.get("/report")
def executive_report(user=Depends(require_role("viewer"))):
    ov = overview(user)   # reaproveita toda a agregação
    now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    sev = ov["open_by_severity"]
    total_open = sum(sev.values())
    fixed = ov["by_status"].get("fixed", 0)

    def rows_html(items, cols):
        return "".join("<tr>" + "".join(f"<td>{c}</td>" for c in cols(i)) + "</tr>" for i in items)

    owasp_rows = rows_html(
        sorted(ov["owasp"].items(), key=lambda x: -x[1]),
        lambda kv: (kv[0], kv[1]))
    risk_rows = rows_html(ov["risk_scores"][:10],
                          lambda r: (r["repo"], r["score"]))
    scan_rows = rows_html(ov["last_scans"],
                          lambda s: (s["repo"], (s.get("last_scan") or "—")[:16].replace("T", " "),
                                     s.get("critical", 0), s.get("high", 0)))

    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>SecPipe — Relatório Executivo</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; color: #1a2433; margin: 40px auto; max-width: 900px; padding: 0 24px; }}
  h1 {{ font-size: 26px; margin-bottom: 2px; }} .sub {{ color: #64748b; font-size: 13px; margin-bottom: 28px; }}
  h2 {{ font-size: 16px; border-bottom: 2px solid #0077cc; padding-bottom: 6px; margin-top: 34px; }}
  .cards {{ display: flex; gap: 14px; margin: 20px 0; flex-wrap: wrap; }}
  .kpi {{ flex: 1; min-width: 110px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; text-align: center; }}
  .kpi b {{ display: block; font-size: 30px; }} .kpi span {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .06em; }}
  .crit b {{ color: #d80f38; }} .high b {{ color: #d06020; }} .med b {{ color: #b98b00; }} .ok b {{ color: #169955; }} .sla b {{ color: #d80f38; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }}
  th {{ text-align: left; font-size: 11px; text-transform: uppercase; color: #64748b; border-bottom: 1px solid #cbd5e1; padding: 6px 8px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #eef2f7; }}
  .noprint {{ margin: 24px 0; }} .noprint button {{ background: #0077cc; color: #fff; border: 0; border-radius: 8px; padding: 10px 22px; font-size: 14px; cursor: pointer; }}
  @media print {{ .noprint {{ display: none; }} body {{ margin: 0; }} }}
  footer {{ margin-top: 40px; font-size: 11px; color: #94a3b8; }}
</style></head><body>
<h1>🛡️ SecPipe — Relatório Executivo de Segurança</h1>
<div class="sub">Gerado em {now_str} · {len(ov["repos"])} projetos monitorados</div>
<div class="noprint"><button onclick="window.print()">🖨 Imprimir / Salvar PDF</button></div>
<div class="cards">
  <div class="kpi"><b>{total_open}</b><span>Abertos</span></div>
  <div class="kpi crit"><b>{sev.get("critical", 0)}</b><span>Critical</span></div>
  <div class="kpi high"><b>{sev.get("high", 0)}</b><span>High</span></div>
  <div class="kpi med"><b>{sev.get("medium", 0)}</b><span>Medium</span></div>
  <div class="kpi ok"><b>{fixed}</b><span>Corrigidos</span></div>
  <div class="kpi sla"><b>{ov["sla_breached"]}</b><span>SLA estourado</span></div>
</div>
<h2>Compliance — OWASP Top 10 (findings abertos)</h2>
<table><tr><th>Categoria</th><th>Findings</th></tr>{owasp_rows or '<tr><td colspan="2">Sem dados — rode novos scans para categorizar</td></tr>'}</table>
<h2>Top 10 projetos por Risk Score</h2>
<p style="font-size:12px;color:#64748b">Score = Σ peso da severidade × fator de idade (até 3× aos 60+ dias) × 1.5 se o repo é público. SLA: critical {ov["sla"]["critical"]}d · high {ov["sla"]["high"]}d · medium {ov["sla"]["medium"]}d · low {ov["sla"]["low"]}d.</p>
<table><tr><th>Projeto</th><th>Risk Score</th></tr>{risk_rows or '<tr><td colspan="2">Nenhum finding aberto 🎉</td></tr>'}</table>
<h2>Último scan por projeto</h2>
<table><tr><th>Projeto</th><th>Último scan</th><th>Critical</th><th>High</th></tr>{scan_rows}</table>
<footer>SecPipe Security Platform — relatório automático. Dados do banco no momento da geração.</footer>
</body></html>"""
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    ico = STATIC / "favicon.ico"
    if ico.exists():
        return FileResponse(ico)
    from fastapi.responses import Response
    # minimal 1×1 transparent ICO
    data = bytes([
        0,0,1,0,1,0,1,1,0,0,1,0,32,0,40,0,0,0,22,0,0,0,40,0,0,0,1,0,0,0,2,0,
        0,0,1,0,32,0,0,0,0,0,4,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
        0,0,255,255,255,0,0,0,0,0
    ])
    return Response(content=data, media_type="image/x-icon")
