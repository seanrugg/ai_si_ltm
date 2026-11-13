"""The hub's downlink (#305): pull from LTMS, publish on the swarm bus.

- Directives are retained on ``<topic_id>/fleet/<id>`` so a member that connects
  later still gets them; a cancel is published on ``<topic_id>/fleet`` and clears
  the retained directive (empty retained message). Expired directives are cleared too.
- Messages addressed to the swarm by members outside it are published unchanged on
  ``<topic_id>/<sender>/say``. The hub doesn't forward them back to LTMS: they're
  already there (``Hub.skip``).
- The cursor (and the open directives) are saved only after publishing, so a crash
  re-delivers rather than loses.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from cli_agent.mqtt_messages import encode_record, fleet_topic, say_topic


class Downlink:
    def __init__(self, client, hub, state_path: str):
        self.client, self.hub, self.state_path = client, hub, state_path
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                s = json.load(f)
            return {"cursor": s.get("cursor"), "open": dict(s.get("open") or {})}
        except (OSError, ValueError):
            return {"cursor": None, "open": {}}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_path)

    def _publish(self, topic_for, payload: bytes, retain: bool) -> None:
        for prefix in self.hub.prefixes:
            self.hub.publish(topic_for(prefix), payload, retain=retain)

    def poll(self, now: datetime | None = None) -> int:
        """One pull; returns items published. Network errors propagate (cursor unchanged)."""
        now = now or datetime.now(timezone.utc)
        published = 0
        while True:
            res = self.client.downlink(self.state["cursor"])
            for item in res.get("items") or []:
                kind = item.get("kind")
                if kind == "directive":
                    d = item["directive"]
                    if d.get("status") != "open":
                        continue
                    body = json.dumps({"kind": "directive", "id": d["id"], "statement": d["statement"],
                                       "signature": d["signature"], "signer": d.get("signer", "fleet"),
                                       "issued_by": d.get("issued_by")}, separators=(",", ":")).encode()
                    self._publish(lambda p, i=d["id"]: fleet_topic(p, i), body, True)
                    self.state["open"][d["id"]] = d["expires_at"]
                elif kind == "cancel":
                    body = json.dumps({"kind": "cancel", "id": item["id"], "statement": item["statement"],
                                       "signature": item["signature"]}, separators=(",", ":")).encode()
                    self._publish(lambda p, i=item["id"]: fleet_topic(p, i), b"", True)  # clear the retained one
                    self._publish(lambda p: fleet_topic(p), body, False)
                    self.state["open"].pop(item["id"], None)
                elif kind == "message":
                    rec = item["record"]
                    self.hub.skip.add(rec["hash"])
                    self._publish(lambda p, a=rec["agent"]: say_topic(a, p), encode_record(rec), False)
                else:
                    continue
                published += 1
            self.state["cursor"] = res.get("cursor") or self.state["cursor"]
            published += self._clear_expired(now)
            self._save()  # only after publishing
            if not res.get("more"):
                return published

    def _clear_expired(self, now: datetime) -> int:
        n = 0
        for did, exp in list(self.state["open"].items()):
            try:
                at = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                at = now
            if at <= now:
                self._publish(lambda p, i=did: fleet_topic(p, i), b"", True)
                del self.state["open"][did]
                logging.info("Hub: directive %s expired; cleared", did)
                n += 1
        return n
