"""A device member with a mind: ``python -m ltms_mind.member`` (#521).

The generic member process (``cli_agent.member_main``) plus the member's mind,
for a device that runs its own models, such as the Mac Studio. The device is the
member: this one process holds its key and signs everything:

- its own records (heartbeats, introductions, directives);
- records its harnesses hand off (Hermes, coding agents), when
  ``MEMBER_HANDOFF_PATH`` is set (``cli_agent.handoff``);
- its mind's records. The mind thinks on its own thread, because a model call
  can take minutes, and this loop signs what it proposes (``ThreadedMind``), so
  the key keeps one writer.

Settings, beyond ``ltms-member``'s and the ``MIND_*`` ones (docs/mind.md):

- ``MEMBER_MIND_SECONDS``: how often the mind wakes (``30m``). Off (``0``) unless set.
- ``MIND_BRAIN=local`` with ``MIND_LOCAL_URL`` / ``MIND_LOCAL_MODEL``: the device's
  model server (Ollama: ``http://127.0.0.1:11434/v1``). The family comes from the
  model name (``gemma4:31b`` -> ``gemma``) unless ``MIND_MODEL_FAMILY`` is set.
- ``MIND_STATE_PATH`` defaults to ``mind.json`` beside ``MEMBER_STATE_PATH``.

At start it reports the device's spec sheet: hardware, and the mind's model as
"in use". Without ``LTMS_SERVER`` there is no mind (it needs to read the fleet).
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping

log = logging.getLogger(__name__)


def build_mind(env: Mapping[str, str], client):
    """The device's mind, or None unless ``MEMBER_MIND_SECONDS`` is set."""
    from ltms_mind.embed import ThreadedMind, embedded_config, parse_every

    every = parse_every(env, "MEMBER_MIND_SECONDS", 0.0)
    if every <= 0:
        return None
    state_dir = os.path.dirname(os.path.abspath(env.get("MEMBER_STATE_PATH", "member-head.json")))
    cfg = embedded_config(env, state_dir)
    mind = ThreadedMind(cfg, every, client)
    log.info("Member: mind every %ss, brain %s%s, family %s", int(every), mind.dream_brain.name,
                 f" ({cfg.local_model})" if cfg.local_model else "", cfg.model_family or "not set")
    return mind


def spec_sheet(env: Mapping[str, str], mind) -> dict:
    """Hardware and software from the machine; models: the mind's, as in use."""
    from cli_agent import hardware

    data_dir = os.path.dirname(os.path.abspath(env.get("MEMBER_OUTBOX_PATH", "member-outbox.db")))
    sheet = hardware.spec_sheet(hardware.detect(data_dir), "ltms-member")
    cfg = mind.mind.cfg if mind is not None else None
    if cfg is not None and cfg.brain == "local" and cfg.local_model:
        runtime = "ollama" if ":11434" in (cfg.local_url or "") else "openai-compatible"
        sheet["models"] = [{"name": cfg.local_model[:64], "runtime": runtime, "where": "onboard", "role": "mind"}]
    return sheet


class AlreadyHosted(RuntimeError):
    """Another process on this device already runs its member (holds ``member.lock``)."""


class DeviceMember:
    """The device's member and mind on a thread of a host process (#521): Hermes's gateway.

    The plugin starts it when Hermes starts and stops it when Hermes stops, so the
    device is in the fleet exactly while Hermes runs. One process per device hosts
    it: ``start()`` takes ``member.lock`` beside ``MEMBER_STATE_PATH`` and raises
    ``AlreadyHosted`` if another process has it (a second Hermes, or
    ``python -m ltms_mind.member``); that process should only hand off records.

    ``set_model(name)``: the model the host is using now (a Hermes session's
    model). The mind thinks with it from its next task, and the spec sheet is
    re-reported, if the model server has it; otherwise the current one stays.
    """

    def __init__(self, env: Mapping[str, str], model: str | None = None, *, client_factory=None,
                 node_factory=None, served=None) -> None:
        self.env = dict(env)
        if model:
            self.env["MIND_LOCAL_MODEL"] = model
        self._client_factory = client_factory
        self._node_factory = node_factory
        self._served = served
        self.mind = None
        self.client = None
        self._lock_fd = None
        self._thread = None
        self._stop = threading.Event()
        self._specs_due = threading.Event()
        self.ready = threading.Event()

    # ------------------------------------------------------------------ the lock
    def _lock_path(self) -> str:
        state_dir = os.path.dirname(os.path.abspath(self.env.get("MEMBER_STATE_PATH", "member-head.json")))
        return os.path.join(state_dir, "member.lock")

    def _take_lock(self) -> None:
        import fcntl
        path = self._lock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyHosted(f"another process runs this device's member ({path})") from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._lock_fd = fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # closing the file releases the lock
            self._lock_fd = None

    # ------------------------------------------------------------------ run
    def start(self) -> None:
        """Take the lock and start the member's thread. Raises ``AlreadyHosted``."""
        self._take_lock()
        try:
            if self._client_factory is not None:
                self.client = self._client_factory(self.env)
            else:
                from cli_agent.client import LTMSClient
                self.client = LTMSClient(self.env["LTMS_SERVER"], key_path=self.env["LTMS_KEY_PATH"])
            try:
                self.mind = build_mind(self.env, self.client)
            except Exception:  # a misconfigured mind must not keep the member down
                log.exception("Device: mind not started")
            self._specs_due.set()
            self._thread = threading.Thread(target=self._run, name="ltms-device-member", daemon=True)
            self._thread.start()
        except BaseException:
            self._release_lock()
            raise

    def _run(self) -> None:
        from cli_agent import member_main
        try:
            cfg = member_main.load_config(self.env)
            make = self._node_factory or (lambda cfg, poll: member_main.MemberNode(cfg, poll=poll, env=self.env))
            node = make(cfg, self._poll)
            node.start()
        except Exception:
            log.exception("Device: member not started")
            return
        if self.mind is not None:
            self.mind.start()
        self.ready.set()
        try:
            while not self._stop.is_set():
                node.step()
                self._stop.wait(1.0)
        except Exception:
            log.exception("Device: member loop stopped")
        finally:
            if self.mind is not None:
                self.mind.stop()
                try:
                    self.mind.pump(node.link)  # what it proposed before stopping
                except Exception:
                    log.exception("Device: last mind records not signed")
            node.stop()

    def _poll(self, link, now) -> None:
        if self._specs_due.is_set():
            self._specs_due.clear()
            try:
                if self.client.report_specs(spec_sheet(self.env, self.mind)) is not None:
                    log.info("Device: spec sheet reported")
            except Exception:  # specs are best effort
                log.exception("Device: spec sheet not reported")
        if self.mind is not None:
            self.mind.pump(link)

    def set_model(self, model: str | None) -> bool:
        """Think with ``model`` from now on, if the model server has it. Returns True if it changed."""
        model = (model or "").strip()
        if not model or self.mind is None or getattr(self.mind.dream_brain, "model", None) == model:
            return False
        served = self._served or _served_models
        known = served(self.mind.mind.cfg.local_url)
        if known is not None and model not in known:
            log.info("Device: %s isn't on the model server; the mind keeps %s", model, self.mind.dream_brain.model)
            return False
        import dataclasses

        from ltms_mind.config import family_of
        cfg = self.mind.mind.cfg
        family = (self.env.get("MIND_MODEL_FAMILY") or "").strip().lower() or family_of(model)
        self.mind.mind.cfg = dataclasses.replace(cfg, local_model=model, model_family=family)
        for brain in {id(b): b for b in (self.mind.dream_brain, self.mind.awake_brain)}.values():
            if hasattr(brain, "model"):
                brain.model = model
        self.env["MIND_LOCAL_MODEL"] = model
        self._specs_due.set()
        log.info("Device: the mind now uses %s (family %s)", model, family or "not set")
        return True

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the member (its handoff is emptied and its last records sent) and free the lock."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._release_lock()


def _served_models(url: str | None) -> set[str] | None:
    """The model names an OpenAI-compatible server lists, or None if it can't be asked."""
    if not url:
        return None
    try:
        import httpx
        r = httpx.get(url.rstrip("/") + "/models", timeout=3.0)
        r.raise_for_status()
        return {m.get("id") for m in r.json().get("data") or [] if m.get("id")}
    except Exception:  # noqa: BLE001 - can't ask: don't block a switch on it
        return None


def main() -> None:
    from cli_agent import member_main
    from cli_agent.client import LTMSClient

    env = os.environ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mind = None
    if env.get("LTMS_SERVER"):
        client = LTMSClient(env["LTMS_SERVER"], key_path=env["LTMS_KEY_PATH"])
        try:
            mind = build_mind(env, client)
        except Exception:  # a misconfigured mind must not keep the member down
            log.exception("Member: mind not started")
        try:
            if client.report_specs(spec_sheet(env, mind)) is not None:
                log.info("Member: spec sheet reported")
        except Exception:  # specs are best effort
            log.exception("Member: spec sheet not reported")

    def poll(link, now) -> None:
        if mind is not None:
            mind.pump(link)  # the mind's records, signed here into the device's chain

    if mind is not None:
        mind.start()
    try:
        member_main.main(poll=poll)
    finally:
        if mind is not None:
            mind.stop()


if __name__ == "__main__":
    main()
