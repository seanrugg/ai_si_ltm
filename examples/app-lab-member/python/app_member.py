"""The App Lab member's logic, kept apart from App Lab so it can be tested anywhere (#299).

- settings from ``data/member.env`` (a template is written on first run);
- the member key (made on the board, mode 0600; only the public key is shown);
- enrollment, sent once and only when the operator sets ``ENROLL=request``;
- waiting for approval (sign-in once a minute);
- folding the MCU's one-second status lines into one record a minute;
- the link state the sketch shows.

Nothing here has a site-specific default (AGENTS.md rule 10).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("app-lab-member")

TEMPLATE = """\
# App Lab LTMS member settings. This file holds a secret (MQTT_PASSWORD): keep it
# mode 600, and empty the app's data/ folder before exporting or sharing the app.

# --- who this board is ---
# Lowercase, unique in the fleet, permanent: ^[a-z0-9][a-z0-9._-]{0,63}$
MEMBER_NAME=
MEMBER_KIND=robot
OPERATOR=
# member or observer (the admin decides what is granted)
ROLE=member
PERMISSIONS=contribute

# --- LTMS ---
LTMS_SERVER=https://ltms.example.org
# Enrollment is sent only when this says "request": it creates a permanent member.
ENROLL=hold
# The swarm this board is meant for (its name, shown to the admin), and an invite code if you have one.
ENROLL_SWARM=
INVITE=

# --- the swarm's hub ---
TRANSPORT=auto
MQTT_HOST=hub.local
MQTT_PORT=1883
# Defaults to MEMBER_NAME
MQTT_USERNAME=
MQTT_PASSWORD=
# The swarm's topic id (sw-xxxxxxxx). Leave empty to look it up from LTMS after approval.
SWARM_TOPIC_ID=
HEARTBEAT_SECONDS=600
\

# --- on-board agent (#492) ---
# on: this board takes tasks and works on them with a model. Every step is signed.
AGENT=off
# brick (App Lab's Large Language Model Brick; add it to the app first), openai (any
# OpenAI-compatible URL, e.g. a llama.cpp or Ollama server), or off
AGENT_BACKEND=brick
AGENT_URL=
AGENT_MODEL=
AGENT_API_KEY=
# Where tasks may come from: any of directives, swarm, console
AGENT_TASKS_FROM=directives,swarm
AGENT_MAX_STEPS=6
AGENT_QUEUE=5
AGENT_PER_SENDER_HOUR=6
AGENT_SAY_PER_TASK=2
AGENT_MODEL_SECONDS=120
# A local web page for typing tasks. It has NO login: anyone who can reach the port can
# give this board tasks. Off by default.
CONSOLE=off
CONSOLE_BIND=127.0.0.1
CONSOLE_PORT=7070
"""

PLACEHOLDERS = {"LTMS_SERVER": "https://ltms.example.org", "MQTT_HOST": "hub.local"}

# Link states (keep in step with sketch.ino)
LINK_STARTING, LINK_PENDING, LINK_ONLINE, LINK_OFFLINE = 0, 1, 2, 3


# ---------------------------------------------------------------------------- settings
def read_env_file(path: Path) -> dict:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def ensure_data_dir(data_dir: Path) -> bool:
    """Create ``data/`` (0700) and the settings template. True if the template was just written."""
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    env_file = data_dir / "member.env"
    if env_file.exists():
        return False
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(TEMPLATE)
    return True


@dataclass(frozen=True)
class Settings:
    name: str
    kind: str
    operator: str | None
    role: str
    permissions: list
    server: str
    enroll: bool
    enroll_swarm: str | None
    invite: str | None
    member_env: dict      # what cli_agent.member_main.load_config reads
    data_dir: Path

    @property
    def key_path(self) -> Path:
        return self.data_dir / "keys" / f"{self.name}.key.json"


def problems(env: dict) -> list[str]:
    """What must be set before this board can do anything. Empty when ready."""
    out = []
    import re
    name = env.get("MEMBER_NAME", "").strip()
    if not name:
        out.append("set MEMBER_NAME")
    elif not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name):
        out.append("MEMBER_NAME must be lowercase letters, digits, '.', '_' or '-'")
    for k, placeholder in PLACEHOLDERS.items():
        if env.get(k, "").strip() in ("", placeholder):
            out.append(f"set {k} (it's still the example value)")
    if env.get("ROLE", "member") not in ("member", "observer"):
        out.append("ROLE must be member or observer")
    return out


def load(data_dir: Path, environ: dict | None = None) -> Settings:
    """Settings from data/member.env, then the environment (which wins). Call ``problems`` first."""
    env = {**read_env_file(data_dir / "member.env"),
           **{k: v for k, v in (os.environ if environ is None else environ).items() if k.isupper()}}
    name = env.get("MEMBER_NAME", "").strip()
    member_env = {k: v for k, v in env.items() if v != ""}
    member_env.setdefault("MQTT_USERNAME", name)
    member_env.update({
        "LTMS_KEY_PATH": str(data_dir / "keys" / f"{name}.key.json"),
        "MEMBER_OUTBOX_PATH": str(data_dir / "member-outbox.db"),
        "MEMBER_STATE_PATH": str(data_dir / "member-head.json"),
    })
    return Settings(
        name=name, kind=env.get("MEMBER_KIND", "robot").strip() or "robot",
        operator=env.get("OPERATOR", "").strip() or None,
        role=env.get("ROLE", "member").strip() or "member",
        permissions=[p.strip() for p in env.get("PERMISSIONS", "contribute").split(",") if p.strip()],
        server=env.get("LTMS_SERVER", "").strip().rstrip("/"),
        enroll=env.get("ENROLL", "hold").strip().lower() == "request",
        enroll_swarm=env.get("ENROLL_SWARM", "").strip() or None,
        invite=env.get("INVITE", "").strip() or None,
        member_env=member_env, data_dir=data_dir)


# ---------------------------------------------------------------------------- key
def ensure_key(settings: Settings) -> str:
    """Make the member key once (0600, never leaves the board). Returns the public key (hex)."""
    from ltms_contract import generate_key, load_key, public_key_hex, save_key

    path = settings.key_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        save_key(str(path), settings.name, generate_key())
        log.info("Made the member key for %s.", settings.name)
    name, key = load_key(str(path))
    if name != settings.name:
        raise SystemExit(f"{path} belongs to {name!r}, not MEMBER_NAME={settings.name!r}")
    return public_key_hex(key)


# ---------------------------------------------------------------------------- enrollment and approval
def enroll_body(settings: Settings, public_key: str, note: str) -> dict:
    body = {"name": settings.name, "kind": settings.kind, "public_key": public_key,
            "permissions": settings.permissions, "operator": settings.operator, "note": note[:1000]}
    if settings.enroll_swarm:
        body["intended"] = {"swarm": settings.enroll_swarm, "role": settings.role}
        body["swarm"] = settings.enroll_swarm
    if settings.invite:
        body["invite"] = settings.invite
    return body


class Approval:
    """Enroll once (if asked), then sign in once a minute until an admin approves.

    Runs on its own thread: network calls never block the App Lab loop. ``phase``
    and ``detail`` say where it is, for the log and the sketch's link state.
    """

    def __init__(self, settings: Settings, client, public_key: str, note: str, *, wait: float = 60.0) -> None:
        self.s, self.client, self.public_key, self.note, self.wait = settings, client, public_key, note, wait
        self.enrolled = settings.data_dir / "enrolled.json"
        self.approved_file = settings.data_dir / "approved.json"
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.phase, self.detail = "starting", ""
        if self.approved_file.exists():
            self.ready.set()
            self.phase = "approved"

    def _set(self, phase: str, detail: str = "") -> None:
        if (phase, detail) != (self.phase, self.detail):
            log.info("%s: %s", phase, detail) if detail else log.info("%s", phase)
        self.phase, self.detail = phase, detail

    def once(self) -> None:
        """One attempt: enroll if needed and allowed, then try to sign in."""
        from cli_agent.client import LTMSError

        if not self.enrolled.exists():
            if not self.s.enroll:
                self._set("awaiting-enroll", f"set ENROLL=request in data/member.env and restart "
                                             f"(public key {self.public_key[:16]}…)")
                return
            try:
                res = self.client.http.post("/api/auth/enroll",
                                            json=enroll_body(self.s, self.public_key, self.note))
            except Exception as exc:  # noqa: BLE001 - network: try again next time
                self._set("enroll-retry", str(exc)[:200])
                return
            if res.status_code in (200, 201, 202, 409):  # 409: this name or key is already known
                self.enrolled.write_text(json.dumps({"at": time.time(), "status": res.status_code}))
                self._set("pending-approval", f"enrollment sent; approve {self.s.name} in the dashboard "
                                              f"(key starts {self.public_key[:16]})")
            else:
                try:
                    err = res.json().get("error", {})
                except ValueError:
                    err = {}
                self._set("enroll-failed", f"{res.status_code} {err.get('code', '')}: {err.get('message', '')}"[:200])
                self.stopping.set()  # don't retry a refusal in a loop; fix settings and restart
                return
        try:
            tok = self.client.login()
        except LTMSError as exc:
            self._set("pending-approval", f"waiting for approval of {self.s.name} "
                                          f"(key starts {self.public_key[:16]}): {exc.code}")
            return
        except Exception as exc:  # noqa: BLE001
            self._set("pending-approval", f"LTMS unreachable: {str(exc)[:120]}")
            return
        self.approved_file.write_text(json.dumps({"at": time.time(), "permissions": tok.get("permissions")}))
        self._set("approved", f"signed in with {tok.get('permissions')}")
        self.ready.set()

    def run(self) -> None:
        while not self.ready.is_set() and not self.stopping.is_set():
            self.once()
            self.stopping.wait(self.wait)


# ---------------------------------------------------------------------------- MCU telemetry
class TelemetryWindow:
    """The sketch's one-second status lines, folded into one summary a minute (#299)."""

    FIELDS = ("uptime_s", "loop_max_us", "link_lost")

    def __init__(self, seconds: float = 60.0, clock=time.monotonic) -> None:
        self.seconds, self.clock = seconds, clock
        self._lock = threading.Lock()
        self._win: dict[str, list[float]] = {}
        self._count = 0
        self._start = clock()
        self.last_at = 0.0
        self.reset_cause: int | None = None

    def add(self, uptime_s, loop_max_us, link_lost, reset_cause) -> None:
        """Called on the Bridge's thread: keep it short."""
        with self._lock:
            for k, v in zip(self.FIELDS, (uptime_s, loop_max_us, link_lost)):
                self._win.setdefault(k, []).append(float(v))
            self._count += 1
            self.last_at = self.clock()
            self.reset_cause = int(reset_cause) if int(reset_cause) >= 0 else None

    def due(self) -> bool:
        return self.clock() - self._start >= self.seconds

    def take(self) -> dict:
        """min/max/last per value since the last take (and clears the window)."""
        now = self.clock()
        with self._lock:
            win, count, self._win, self._count, self._start = self._win, self._count, {}, 0, now
        out = {"kind": "mcu", "samples": count, "window_s": self.seconds,
               "reset_cause": self.reset_cause if self.reset_cause is not None else "unknown"}
        if count:
            out["values"] = {k: {"min": min(v), "max": max(v), "last": v[-1]} for k, v in sorted(win.items())}
        else:
            out["silent_s"] = round(now - self.last_at, 1) if self.last_at else None
        return out


def link_state(phase: str, member_status: dict | None) -> int:
    if member_status and member_status.get("running"):
        return LINK_ONLINE if member_status.get("mqtt") or member_status.get("transport") == "direct" else LINK_OFFLINE
    if phase in ("awaiting-enroll", "pending-approval", "needs-settings"):
        return LINK_PENDING
    if phase in ("enroll-failed", "member-failed"):
        return LINK_OFFLINE
    return LINK_STARTING


def hardware_note(public_key: str) -> str:
    """The enrollment note: what this is, the key fingerprint and a hardware summary (#296)."""
    try:
        from cli_agent import hardware
        summary = hardware.summary(hardware.detect("."))
    except Exception:  # noqa: BLE001 - the note is best effort
        summary = "hardware: unknown"
    return f"Arduino App Lab LTMS member (examples/app-lab-member). Key {public_key[:16]}. {summary}"
