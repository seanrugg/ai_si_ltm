"""Which MQTT topic prefix a hub or member uses for its swarm (#293).

Topics are ``<topic_id>/<agent>/...``. The topic ID comes from the swarm's
permanent ID (``GET /api/swarm``, #292), so renaming a swarm changes nothing
on any device. Precedence:

1. ``SWARM_TOPIC_ID`` from the environment;
2. otherwise LTMS (``LTMSClient.swarm()``), when ``LTMS_SERVER`` is set;
3. otherwise the old ``SWARM=<name>``, with a deprecation warning.
"""
from __future__ import annotations

import logging
import re

_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")  # no '/', '+' or '#': one MQTT topic level


class NoSwarmTopic(ValueError):
    """Nothing configured and LTMS couldn't say which swarm this is."""


def check_prefix(value: str, setting: str) -> str:
    if not _SAFE.match(value or ""):
        raise ValueError(f"{setting} must be one MQTT topic level: letters, digits, '.', '_' or '-'")
    return value


def resolve(topic_id: str | None, name: str | None, lookup=None) -> tuple[str, str]:
    """Return ``(prefix, source)``, source being "config", "ltms" or "name".

    ``lookup`` is a callable returning ``LTMSClient.swarm()``'s dict (or None);
    it's only called when ``topic_id`` isn't set.
    """
    if topic_id:
        return check_prefix(topic_id, "SWARM_TOPIC_ID"), "config"
    if lookup is not None:
        swarm = lookup()
        if swarm and swarm.get("topic_id"):
            return check_prefix(swarm["topic_id"], "the topic_id from LTMS"), "ltms"
    if name:
        logging.warning(
            "SWARM=%s: addressing the swarm by name is deprecated; a rename would split the bus. "
            "Set SWARM_TOPIC_ID (see GET /api/swarm) or LTMS_SERVER instead.", name)
        return check_prefix(name, "SWARM"), "name"
    raise NoSwarmTopic(
        "no swarm topic: set SWARM_TOPIC_ID, or LTMS_SERVER so it can be looked up "
        "(this member must already be in a swarm), or the deprecated SWARM")
