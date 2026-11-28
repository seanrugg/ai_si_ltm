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
import sys
from pathlib import Path

log = logging.getLogger("ltms_recorder")

PLUGIN_VERSION = "0.3.0"
HOOKS = ("on_session_start", "post_tool_call", "on_session_end")
ROLLUP_HOOKS = ("post_api_request", "api_request_error", "post_compaction")


CLIENT_PACKAGES = ("cli_agent", "ltms_contract")  # device mode (#521) also imports ltms_mind from the checkout


def checkout_root() -> Path | None:
    """The LTMS repository this plugin lives in, if it holds the client packages.

    Symlinks are resolved, so a plugin linked into ``~/.hermes/plugins/`` from a
    checkout finds that checkout (``<repo>/integrations/hermes/ltms_recorder``).
    """
    try:
        root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    if all((root / pkg / "__init__.py").is_file() for pkg in CLIENT_PACKAGES):
        return root
    return None


def use_checkout_client() -> Path | None:
    """Make ``cli_agent`` and ``ltms_contract`` importable from the checkout (#478).

    An installed ``ltms-harness`` wins: the checkout is only added when the
    packages can't be imported. It is appended, never prepended, so it can't
    shadow anything Hermes ships. Returns the root it added, or None.
    """
    try:
        import cli_agent  # noqa: F401
        import ltms_contract  # noqa: F401
        import ltms_mind  # noqa: F401
        return None
    except ImportError:
        pass
    root = checkout_root()
    if root is not None and str(root) not in sys.path:
        sys.path.append(str(root))
        return root
    return None


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
    """Hermes entry point. Never raises: a broken recorder must not stop Hermes.

    With ``LTMS_DEVICE_ENV`` set, device mode (#521, ``device.py``): Hermes records as
    the device through its handoff, and runs the device's member and mind while it runs.
    """
    device = None
    try:
        use_checkout_client()
        try:
            settings = ctx.get_config() or {}
        except Exception:  # noqa: BLE001 - older Hermes, or no settings saved yet
            settings = {}
        if os.environ.get("LTMS_DEVICE_ENV"):
            from .device import start_device
            recorder, device = start_device(settings)
        else:
            recorder = build_recorder(settings)
        rollup = build_rollup() if rollup_enabled(settings) else None
    except KeyError as exc:
        log.warning("ltms_recorder: not started, %s is not set", exc.args[0])
        return
    except ImportError as exc:
        missing = getattr(exc, "name", None) or str(exc)
        if missing in CLIENT_PACKAGES or (missing or "").split(".")[0] in CLIENT_PACKAGES:
            hint = ("link the plugin from an LTMS checkout, or install ltms-harness into Hermes's environment "
                    "(see README)")
        else:
            hint = "run `hermes plugins enable ltms_recorder` again to install its dependencies (see README)"
        log.warning("ltms_recorder: not started, cannot import %s: %s", missing, hint)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("ltms_recorder: not started (%s)", type(exc).__name__)
        return

    hooks = wire(recorder, rollup)
    if os.environ.get("LTMS_DEVICE_ENV"):
        from .device import follow_session_model
        hooks["on_session_start"] = follow_session_model(hooks["on_session_start"], device)
    for name, fn in hooks.items():
        ctx.register_hook(name, fn)

    def close() -> None:
        if rollup is not None:
            rollup.close(recorder)
        recorder.close()
        if device is not None:
            device.stop()  # after the recorder: its last records are in the handoff, signed on the way out

    atexit.register(close)
    log.info("ltms_recorder: recording as %s (mode %s, rollup %s%s)", recorder.client.name,
             recorder.settings["record_mode"], "on" if rollup is not None else "off",
             "" if not os.environ.get("LTMS_DEVICE_ENV") else
             (", device member running here" if device is not None else ", device member runs in another process"))
