"""ltms_recorder: a Hermes Agent plugin that records sessions and tool calls to LTMS (#469),
plus a daily usage rollup (#470).

Hermes loads this directory from ``~/.hermes/plugins/ltms_recorder/`` and calls
``register(ctx)``. The logic lives in ``recorder.py`` and ``rollup.py``; this
file only wires them to Hermes hooks. See ``integrations/hermes/README.md``.
"""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

log = logging.getLogger("ltms_recorder")

PLUGIN_VERSION = "0.2.0"
HOOKS = ("on_session_start", "post_tool_call", "on_session_end")
ROLLUP_HOOKS = ("post_api_request", "api_request_error", "post_compaction")


def _data_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "plugin-data" / "ltms_recorder"


def build_recorder(settings: dict | None = None, *, background: bool = True):
    """Create the client and recorder from ``LTMS_SERVER`` and ``LTMS_KEY_PATH``.

    No network call happens here: the chain is synced on the first flush, so
    Hermes starts normally even when LTMS is unreachable.
    """
    from cli_agent.client import LTMSClient
    from cli_agent.outbox import Outbox

    from .recorder import Recorder

    server = os.environ["LTMS_SERVER"]
    key_path = os.environ["LTMS_KEY_PATH"]
    data = _data_dir()
    data.mkdir(parents=True, exist_ok=True)
    client = LTMSClient(server, key_path=key_path, timeout=5.0)
    return Recorder(
        client,
        settings=settings,
        outbox=Outbox(data / "outbox.db"),
        head_path=data / "head.json",
        background=background,
    )


def rollup_enabled(settings: dict | None) -> bool:
    return (settings or {}).get("rollup", True) is not False


def build_rollup():
    from .rollup import Rollup
    return Rollup(_data_dir(), plugin_version=PLUGIN_VERSION)


def wire(recorder, rollup) -> dict:
    """Hook name -> callable. Each one feeds the recorder, then the rollup, and never raises."""
    if rollup is None:
        return {"on_session_start": recorder.on_session_start,
                "post_tool_call": recorder.on_post_tool_call,
                "on_session_end": recorder.on_session_end}

    def both(record, count):
        def hook(*args, **kwargs):
            record(*args, **kwargs)
            count(*args, **kwargs)
            rollup.emit_due(recorder)  # local signing only; the recorder's thread sends
        return hook

    def count_only(count):
        def hook(*args, **kwargs):
            count(*args, **kwargs)
            rollup.emit_due(recorder)
        return hook

    return {"on_session_start": both(recorder.on_session_start, rollup.on_session_start),
            "post_tool_call": both(recorder.on_post_tool_call, rollup.on_post_tool_call),
            "on_session_end": both(recorder.on_session_end, rollup.on_session_end),
            "post_api_request": count_only(rollup.on_post_api_request),
            "api_request_error": count_only(rollup.on_api_request_error),
            "post_compaction": count_only(rollup.on_post_compaction)}


def register(ctx) -> None:
    """Hermes entry point. Never raises: a broken recorder must not stop Hermes."""
    try:
        try:
            settings = ctx.get_config() or {}
        except Exception:  # noqa: BLE001 - older Hermes, or no settings saved yet
            settings = {}
        recorder = build_recorder(settings)
        rollup = build_rollup() if rollup_enabled(settings) else None
    except KeyError as exc:
        log.warning("ltms_recorder: not started, %s is not set", exc.args[0])
        return
    except ImportError:
        log.warning("ltms_recorder: not started, install ltms-harness into Hermes's Python (see README)")
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("ltms_recorder: not started (%s)", type(exc).__name__)
        return

    for name, fn in wire(recorder, rollup).items():
        ctx.register_hook(name, fn)

    def close() -> None:
        if rollup is not None:
            rollup.close(recorder)
        recorder.close()

    atexit.register(close)
    log.info("ltms_recorder: recording as %s (mode %s, rollup %s)", recorder.client.name,
             recorder.settings["record_mode"], "on" if rollup is not None else "off")
