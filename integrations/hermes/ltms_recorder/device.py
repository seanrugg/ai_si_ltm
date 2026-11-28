"""Device mode (#521): Hermes records as the device it runs on, and runs that device's member.

The device is the member, not the harness. With ``LTMS_DEVICE_ENV`` set (the
device's settings file, e.g. ``~/.ltms/device/device.env`` on the Mac Studio):

- **The device's member runs while Hermes runs.** The plugin starts it (heartbeats,
  directives, spec sheet, and its mind) on a thread of Hermes's gateway when
  Hermes starts, and stops it when Hermes exits. One process per device hosts it
  (``member.lock``); a second Hermes process only hands off its records.
- **The mind thinks with Hermes's model.** It starts with ``model.default`` from
  Hermes's ``config.yaml`` and follows each session's model, if the model server
  has it.
- **Hermes's records go through the device's handoff** (``MEMBER_HANDOFF_PATH``):
  the plugin never signs, so the device's chain has one writer.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("ltms_recorder")

SOURCE = "hermes"


def load_env_file(path: str | os.PathLike) -> dict[str, str]:
    """``KEY=value`` lines (``#`` comments, blank lines, optional quotes), as ``sh`` would read them."""
    env: dict[str, str] = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            env[key] = os.path.expanduser(value)
    return env


def hermes_model(hermes_home: str | os.PathLike | None = None) -> str | None:
    """Hermes's selected model: ``model.default`` in ``$HERMES_HOME/config.yaml``."""
    home = Path(hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    path = home / "config.yaml"
    try:
        text = path.read_text()
    except OSError:
        return None
    try:
        import yaml
        model = (yaml.safe_load(text) or {}).get("model")
        value = model.get("default") if isinstance(model, dict) else model
        return (str(value).strip() or None) if value else None
    except Exception:  # noqa: BLE001 - no PyYAML, or an odd file: read the one line
        m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+default:\s*['\"]?([^'\"\n#]+)", text, re.MULTILINE)
        return m.group(1).strip() if m else None


class HandoffClient:
    """What the recorder (and its rollup) sign with in device mode: records go to the
    device's handoff and the member process signs them. Nothing here holds a key."""

    def __init__(self, handoff, name: str) -> None:
        self.handoff, self.name = handoff, name
        self.outbox: list[dict] = []   # the handoff is the queue; nothing waits here
        self.seq = 0

    def append(self, type: str, *, payload: dict | None = None, summary: str | None = None,
               ts: str | None = None, **header: Any) -> dict:
        rid = self.handoff.submit(type, summary=summary, payload=payload, ts=ts, source=SOURCE, **header)
        return {"seq": rid, "handoff_id": rid}

    def flush(self, batch_size: int = 100) -> list[dict]:
        return []


def make_handoff_recorder(handoff, name: str, settings: dict | None, background: bool = True):
    """A ``Recorder`` whose records go to the device's handoff instead of being signed here."""
    from .recorder import Recorder

    class HandoffRecorder(Recorder):
        def __init__(self) -> None:
            super().__init__(HandoffClient(handoff, name), settings=settings, background=background)
            self.ready = True

        def _try_start(self, allow_network: bool = True) -> bool:
            return True

        @property
        def queued(self) -> int:
            return len(self._pending)

        def flush(self) -> int:
            try:
                return self.sign_pending()
            except Exception as exc:  # noqa: BLE001 - never break Hermes
                log.warning("ltms_recorder: handoff failed (%s)", type(exc).__name__)
                return 0

    return HandoffRecorder()


def start_device(settings: dict | None, *, device_factory=None, background: bool = True):
    """Device mode: ``(recorder, device or None)``. ``device`` is None when another
    process already runs this device's member; then this one only hands off."""
    from cli_agent.handoff import Handoff
    from ltms_contract import load_key

    env = {**load_env_file(os.environ["LTMS_DEVICE_ENV"])}
    for key in ("LTMS_SERVER", "LTMS_KEY_PATH", "MEMBER_HANDOFF_PATH"):
        if not env.get(key):
            raise KeyError(key)
    name, _ = load_key(env["LTMS_KEY_PATH"])
    recorder = make_handoff_recorder(Handoff(env["MEMBER_HANDOFF_PATH"]), name, settings, background)

    if device_factory is None:
        from ltms_mind.member import DeviceMember as device_factory
    from ltms_mind.member import AlreadyHosted

    device = device_factory(env, hermes_model())
    try:
        device.start()
    except AlreadyHosted as exc:
        log.info("ltms_recorder: %s; recording through its handoff only", exc)
        device = None
    return recorder, device


def follow_session_model(start_hook, device):
    """``on_session_start`` that records the session (``start_hook``) and moves the mind to its model."""
    def hook(*args: Any, **kwargs: Any) -> None:
        start_hook(*args, **kwargs)
        if device is not None:
            try:
                device.set_model(kwargs.get("model"))
            except Exception as exc:  # noqa: BLE001 - never break Hermes
                log.warning("ltms_recorder: model not followed (%s)", type(exc).__name__)
    return hook


__all__ = ["HandoffClient", "follow_session_model", "hermes_model", "load_env_file",
           "make_handoff_recorder", "start_device"]
