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
import contextlib
import json as _json
import os
import secrets
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

import hashlib
import pyotp

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
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

def _auto_register_tunnel():
    """Background: polls cloudflared /quicktunnel and updates GitHub variable SECPIPE_DASHBOARD_URL."""
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
            payload = _json.dumps({"name": "SECPIPE_DASHBOARD_URL", "value": url}).encode()
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{gh_repo}/actions/variables/SECPIPE_DASHBOARD_URL",
                    data=payload, method="PATCH", headers=headers,
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    req = urllib.request.Request(
                        f"https://api.github.com/repos/{gh_repo}/actions/variables",
                        data=_json.dumps({"name": "SECPIPE_DASHBOARD_URL", "value": url}).encode(),
                        method="POST", headers=headers,
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        r.read()
                else:
                    raise
            print(f"[secpipe] SECPIPE_DASHBOARD_URL → {url}", flush=True)
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
    return {
        "repos": repos,
        "open_by_severity": {sev: totals.get(sev, 0) for sev in SEVERITIES},
        "by_status": by_status,
        "last_scans": last_scans,
        "risk": risk,
        "engines": engines,
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
    return {"ok": True}


@app.post("/api/repos/{repo:path}/dispatch")
def dispatch_scan(repo: str, user=Depends(require_role("analyst"))):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN não configurado")
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    payload = _json.dumps({"ref": "main"}).encode()
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/workflows/security.yml/dispatches",
            data=payload,
            headers=hdrs,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 422:
            # tenta branch master
            try:
                payload2 = _json.dumps({"ref": "master"}).encode()
                req2 = urllib.request.Request(
                    f"https://api.github.com/repos/{repo}/actions/workflows/security.yml/dispatches",
                    data=payload2, headers=hdrs, method="POST",
                )
                with urllib.request.urlopen(req2, timeout=10):
                    pass
            except urllib.error.HTTPError as e2:
                raise HTTPException(status_code=e2.code, detail=e2.read().decode(errors="replace"))
        else:
            raise HTTPException(status_code=e.code, detail=body)
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "repo": repo}


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
    try:
        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=30"
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode(errors="replace"))
    except urllib.error.URLError as e:
        raise HTTPException(502, str(e))

    jobs = []
    for job in data.get("jobs", []):
        failed_steps = [
            {"name": s["name"], "number": s["number"]}
            for s in job.get("steps", [])
            if s.get("conclusion") in ("failure", "timed_out")
        ]
        jobs.append({
            "name": job["name"],
            "conclusion": job.get("conclusion"),
            "status": job.get("status"),
            "failed_steps": failed_steps,
            "url": job.get("html_url", ""),
            "completed_at": job.get("completed_at", ""),
        })
    return {"jobs": jobs}


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
