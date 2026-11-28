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
