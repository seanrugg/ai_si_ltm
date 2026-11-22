"""System status (#383): a read-only health page for the admin, after a deploy.

What this may return is deliberately narrow. Never: environment variables,
secrets, tokens, private key material, hostnames, IPs, member data, or file
paths. The one thing about the filesystem it reports is the data directory's
free and total space, in bytes.

Background jobs: the scheduler's run wrapper (``server.tasks``) calls
:func:`record_job` after every run. Results are kept in memory, so they start
empty after a restart; next-run times come from the live scheduler.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, inspect, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from server import __version__
from server.models import LrsEndpoint, McpToken, Swarm
from server.services import fleet_key, reference_sources

# Friendly names for the scheduler's jobs; an unknown job shows its own name.
JOB_LABELS = {
    "purge_expired_auth": "Expired sign-ins cleanup",
    "purge_payloads": "Data retention: payload purge",
    "prune_audit_log": "Data retention: audit log",
    "prune_member_addresses": "Member address pruning",
    "expire_dataset_uploads": "Unfinished dataset uploads",
    "reference_refresh": "Reference source refresh",
    "geoip_refresh": "City database refresh",
    "observe_cognition": "Cognition observation",
    "lrs_forward": "LRS sync and summary",
    "enroll_auto_denied": "Auto-denied enrollment summary",
    "enroll_expire_pending": "Pending enrollment expiry",
}

MAX_MESSAGE = 200

# Order matters: URLs first (they may hold user:password@host), then addresses,
# paths and anything that looks like a key or token.
_SCRUB = [
    (re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+"), "<url>"),
    (re.compile(r"\S+@\S+"), "<redacted>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b"), "<ip>"),
    (re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w.~-]+){2,}[/\\]?"), "<path>"),
    (re.compile(r"\b[A-Za-z0-9+/_=-]{24,}\b"), "<redacted>"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*\S+"), r"\1=<redacted>"),
]


def scrub(text: str | None) -> str | None:
    """One line, at most MAX_MESSAGE characters, with URLs, IPs, paths and
    token-like strings replaced. Used for job errors and the diagnostics text."""
    if text is None:
        return None
    line = next((ln.strip() for ln in str(text).splitlines() if ln.strip()), "")
    for pattern, repl in _SCRUB:
        line = pattern.sub(repl, line)
    return line[:MAX_MESSAGE]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# --- background jobs ---------------------------------------------------------

def record_job(state, name: str, error: BaseException | None = None, *, at: datetime | None = None) -> None:
    """Remember a job's latest run on ``app.state.job_runs``. Never raises."""
    try:
        runs = getattr(state, "job_runs", None)
        if runs is None:
            runs = {}
            state.job_runs = runs
        if error is None:
            runs[name] = {"last_run": at or _now(), "result": "ok", "message": None}
        else:
            message = scrub(str(error)) or ""
            message = f"{type(error).__name__}: {message}" if message else type(error).__name__
            runs[name] = {"last_run": at or _now(), "result": "error", "message": message[:MAX_MESSAGE]}
    except Exception:  # noqa: BLE001 - status bookkeeping must never break a job
        pass


def jobs(state) -> dict:
    sched = getattr(state, "scheduler", None)
    runs = dict(getattr(state, "job_runs", None) or {})
    rows: dict[str, dict] = {}
    if sched is not None:
        for job in sched.get_jobs():
            name = job.args[0] if job.args and isinstance(job.args[0], str) else job.id
            rows[name] = {"name": name, "label": JOB_LABELS.get(name, name),
                          "next_run": _iso(getattr(job, "next_run_time", None)),
                          "last_run": None, "result": None, "message": None}
    for name, run in runs.items():
        row = rows.setdefault(name, {"name": name, "label": JOB_LABELS.get(name, name), "next_run": None})
        row.update(last_run=_iso(run["last_run"]), result=run["result"], message=run["message"])
    out = sorted(rows.values(), key=lambda r: r["label"].lower())
    return {"scheduler_running": bool(sched is not None and getattr(sched, "running", False)),
            "errors": sum(1 for r in out if r.get("result") == "error"),
            "jobs": out}


# --- server ------------------------------------------------------------------

def _git_dir(start: Path) -> Path | None:
    for d in (start, *start.parents):
        g = d / ".git"
        if g.is_dir():
            return g
        if g.is_file():  # a worktree: "gitdir: <path>"
            text = g.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                return (d / text[len("gitdir:"):].strip()).resolve()
    return None


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    """The checked-out commit (12 hex characters) if the code runs from a git
    checkout, else None. Read from files; git is never run. Only the hash leaves."""
    try:
        g = _git_dir(Path(__file__).resolve().parent)
        if g is None:
            return None
        head = (g / "HEAD").read_text(encoding="utf-8").strip()
        sha = head
        if head.startswith("ref:"):
            ref = head[4:].strip()
            common = g
            if (g / "commondir").is_file():
                common = (g / (g / "commondir").read_text(encoding="utf-8").strip()).resolve()
            sha = None
            for base in (g, common):
                if (base / ref).is_file():
                    sha = (base / ref).read_text(encoding="utf-8").strip()
                    break
            if sha is None and (common / "packed-refs").is_file():
                for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        sha = parts[0]
                        break
        if sha and re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", sha):
            return sha[:12]
    except OSError:
        pass
    return None


def _disk(data_dir: str) -> dict:
    """Free and total bytes on the data directory's filesystem (its nearest
    existing parent if it isn't made yet). Never the path itself."""
    try:
        p = Path(data_dir).resolve()
        while not p.exists() and p != p.parent:
            p = p.parent
        usage = shutil.disk_usage(p)
        return {"free_bytes": usage.free, "total_bytes": usage.total}
    except OSError:
        return {"free_bytes": None, "total_bytes": None}


def database_kind(cfg) -> str:
    try:
        return make_url(cfg.resolved_database_url).get_backend_name()
    except Exception:  # noqa: BLE001 - never echo a malformed URL
        return "unknown"


# Where each deployment keeps its settings (#390); docs/configuration.md has the list.
DEPLOYMENTS = {
    "docker": {"label": "Docker", "settings_file": ".env next to compose.yaml",
               "apply": "docker compose up -d"},
    "service": {"label": "systemd service", "settings_file": "/etc/ltms/ltms.env",
                "apply": "restart the service"},
    "other": {"label": "other", "settings_file": "this process's environment variables",
              "apply": "restart LTMS"},
}


def deployment_mode(cfg, *, root: str = "/", environ=None) -> str:
    """``docker``, ``service`` or ``other``: LTMS_DEPLOYMENT when it names one, else detected."""
    environ = os.environ if environ is None else environ
    mode = (getattr(cfg, "deployment", "") or "").strip().lower()
    if mode in ("docker", "service"):
        return mode
    if os.path.exists(os.path.join(root, ".dockerenv")):
        return "docker"
    if environ.get("INVOCATION_ID"):  # set by systemd for every unit it starts
        return "service"
    return "other"


def deployment(cfg) -> dict:
    mode = deployment_mode(cfg)
    return {"mode": mode, **DEPLOYMENTS[mode]}


def server(state, cfg) -> dict:
    started = getattr(state, "started_at", None)
    now = _now()
    return {
        "version": __version__,
        "commit": git_commit(),
        "started_at": _iso(started),
        "uptime_seconds": int((now - started).total_seconds()) if started else None,
        "database": database_kind(cfg),
        "data_dir": _disk(cfg.data_dir),
        "deployment": deployment(cfg),
    }


# --- database ----------------------------------------------------------------

def code_head() -> str | None:
    """The newest migration revision this code knows about."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", "server:migrations")
    return ScriptDirectory.from_config(cfg).get_current_head()


async def database(session: AsyncSession, cfg) -> dict:
    """``managed`` is False when the schema is made directly (SQLite, or
    ``schema_mode=create``) and there's no Alembic version table to compare."""
    from alembic.runtime.migration import MigrationContext

    def read(sync_session):
        conn = sync_session.connection()
        if not inspect(conn).has_table("alembic_version"):
            return False, None
        return True, MigrationContext.configure(conn).get_current_revision()

    managed, current = await session.run_sync(read)
    head = code_head()
    pending = bool(managed and current != head)
    return {"kind": database_kind(cfg), "managed": managed, "current": current, "head": head,
            "pending": pending,
            "warning": f"migrations pending: database at {current or 'none'}, code needs {head}" if pending else None}


# --- keys --------------------------------------------------------------------

def fingerprint(public_key: bytes) -> str:
    """OpenSSH-style: SHA256 of the public key, base64 without padding."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(public_key).digest()).decode().rstrip("=")


async def keys(session: AsyncSession, cfg) -> dict:
    """Only the public half is used. A missing key is reported, never created."""
    path = fleet_key.path_for(cfg)
    fleet = {"present": False, "fingerprint": None, "created_at": None,
             "backup_note": "Back up fleet-key.json together with the data directory. "
                            "Without it, members can't verify directives signed before a restore."}
    if path.is_file():
        try:
            fleet["fingerprint"] = fingerprint(bytes.fromhex(fleet_key.public_hex(cfg)))
            fleet["created_at"] = _iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            fleet["present"] = True
        except Exception:  # noqa: BLE001 - unreadable key: report it without detail
            fleet["error"] = "The fleet key file exists but can't be read."
    active = Swarm.status == "active"
    swarms = await session.scalar(select(func.count()).select_from(Swarm).where(active)) or 0
    led = await session.scalar(select(func.count()).select_from(Swarm)
                               .where(active, Swarm.leader_member_id.is_not(None))) or 0
    return {"fleet_key": fleet, "swarms": int(swarms), "swarms_with_leader": int(led)}


# --- integrations ------------------------------------------------------------

async def _count(session, model, *where) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def integrations(session: AsyncSession, cfg) -> dict:
    now = _now()
    lrs_total = await _count(session, LrsEndpoint)
    lrs_enabled = await _count(session, LrsEndpoint, LrsEndpoint.enabled.is_(True))
    lrs_failing = await _count(session, LrsEndpoint, LrsEndpoint.enabled.is_(True), LrsEndpoint.last_error.is_not(None))
    lrs_state = "none" if lrs_total == 0 else ("warning" if lrs_failing else "ok")

    mcp_total = await _count(session, McpToken)
    mcp_revoked = await _count(session, McpToken, McpToken.revoked_at.is_not(None))
    mcp_expired = await _count(session, McpToken, McpToken.revoked_at.is_(None),
                               McpToken.expires_at.is_not(None), McpToken.expires_at <= now)
    mcp_active = await _count(session, McpToken, McpToken.revoked_at.is_(None),
                              or_(McpToken.expires_at.is_(None), McpToken.expires_at > now))
    mcp_state = "none" if mcp_total == 0 else "ok"

    sources = await reference_sources.status(session, cfg)
    imported = sum(1 for s in sources if s.get("dataset_id"))
    pending = sum(1 for s in sources for p in s.get("publications", []) if p.get("status") == "pending")
    ref_state = "none" if imported == 0 else ("warning" if pending else "ok")

    return {
        "lrs": {"label": "LRS endpoints", "page": "lrs", "state": lrs_state, "total": lrs_total,
                "enabled": lrs_enabled, "paused": lrs_total - lrs_enabled, "failing": lrs_failing},
        "mcp": {"label": "MCP tokens", "page": "access", "state": mcp_state, "total": mcp_total,
                "active": mcp_active, "revoked": mcp_revoked, "expired": mcp_expired},
        "reference_sources": {"label": "Reference sources", "page": "datasets", "state": ref_state,
                              "total": len(sources), "imported": imported, "pending_versions": pending,
                              "refresh_hours": cfg.reference_refresh_hours},
    }


# --- diagnostics -------------------------------------------------------------

def _gb(n: int | None) -> str:
    return "unknown" if n is None else f"{n / 1e9:.1f} GB"


def diagnostics(out: dict) -> str:
    """Plain text for a bug report, built only from the (already safe) sections."""
    s, db, j, k, i = out["server"], out["database"], out["jobs"], out["keys"], out["integrations"]
    lines = [
        "FLTMS system status",
        f"generated: {out['generated_at']}",
        f"version: {s['version']}" + (f" ({s['commit']})" if s["commit"] else ""),
        f"started: {s['started_at'] or 'unknown'}; uptime seconds: {s['uptime_seconds']}",
        f"database: {s['database']}",
        f"deployment: {s['deployment']['mode']} (settings: {s['deployment']['settings_file']})",
        f"data dir free: {_gb(s['data_dir']['free_bytes'])} of {_gb(s['data_dir']['total_bytes'])}",
        "migrations: " + (f"{db['current'] or 'none'} (code head {db['head']})" if db["managed"]
                          else f"not managed by Alembic (code head {db['head']})")
        + (" PENDING" if db["pending"] else ""),
        f"scheduler: {'running' if j['scheduler_running'] else 'off'}; jobs with errors: {j['errors']}",
    ]
    for row in j["jobs"]:
        lines.append(f"  job {row['name']}: {row.get('result') or 'not run yet'}"
                     + (f" ({scrub(row['message'])})" if row.get("message") else "")
                     + f"; last {row.get('last_run') or '-'}; next {row.get('next_run') or '-'}")
    fk = k["fleet_key"]
    lines.append(f"fleet key: {'present' if fk['present'] else 'not created yet'}"
                 + (f", created {fk['created_at']}" if fk["created_at"] else ""))
    lines.append(f"swarms: {k['swarms']}, with a leader: {k['swarms_with_leader']}")
    lrs, mcp, ref = i["lrs"], i["mcp"], i["reference_sources"]
    lines.append(f"LRS endpoints: {lrs['total']} ({lrs['enabled']} enabled, {lrs['failing']} failing)")
    lines.append(f"MCP tokens: {mcp['total']} ({mcp['active']} active, {mcp['revoked']} revoked, {mcp['expired']} expired)")
    lines.append(f"reference sources: {ref['imported']} of {ref['total']} imported, {ref['pending_versions']} pending versions")
    return "\n".join(lines) + "\n"


async def status(session: AsyncSession, state, cfg) -> dict:
    out = {
        "generated_at": _iso(_now()),
        "server": server(state, cfg),
        "database": await database(session, cfg),
        "jobs": jobs(state),
        "keys": await keys(session, cfg),
        "integrations": await integrations(session, cfg),
    }
    out["diagnostics"] = diagnostics(out)
    return out
