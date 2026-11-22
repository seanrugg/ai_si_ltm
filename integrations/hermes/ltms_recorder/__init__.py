"""ltms_recorder: a Hermes Agent plugin that records sessions and tool calls to LTMS (#469).

Hermes loads this directory from ``~/.hermes/plugins/ltms_recorder/`` and calls
``register(ctx)``. All the logic lives in ``recorder.py``; this file only wires
it to Hermes hooks. See ``integrations/hermes/README.md``.
"""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

log = logging.getLogger("ltms_recorder")

HOOKS = ("on_session_start", "post_tool_call", "on_session_end")


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


def register(ctx) -> None:
    """Hermes entry point. Never raises: a broken recorder must not stop Hermes."""
    try:
        try:
            settings = ctx.get_config() or {}
        except Exception:  # noqa: BLE001 - older Hermes, or no settings saved yet
            settings = {}
        recorder = build_recorder(settings)
    except KeyError as exc:
        log.warning("ltms_recorder: not started, %s is not set", exc.args[0])
        return
    except ImportError:
        log.warning("ltms_recorder: not started, install ltms-harness into Hermes's Python (see README)")
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("ltms_recorder: not started (%s)", type(exc).__name__)
        return

    ctx.register_hook("on_session_start", recorder.on_session_start)
    ctx.register_hook("post_tool_call", recorder.on_post_tool_call)
    ctx.register_hook("on_session_end", recorder.on_session_end)
    atexit.register(recorder.close)
    log.info("ltms_recorder: recording as %s (mode %s)", recorder.client.name, recorder.settings["record_mode"])
