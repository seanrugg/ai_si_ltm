"""Reference member client. Z3-R0's relay, Vector's adapter and cloud agents can
copy this: it is the whole member side of the protocol in ~150 lines.

    client = LTMSClient("https://ltms.example", key_path="keys/z3-r0.json")
    client.login()
    client.sync_head()
    client.append("action", summary="Approach Vector", peers=["vector"],
                  outcome="reflex_stop", payload={"distance_m": 0.8})
    client.flush()
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import json
import logging
import os
import random
import socket
import time
import uuid
from urllib.parse import quote

import httpx
from typing import Callable

from ltms_contract import GENESIS_HASH, auth_signing_message, build_record, load_key, public_key_hex


log = logging.getLogger(__name__)


def local_addresses() -> list[dict]:
    """This host's non-loopback, non-link-local addresses, via socket.getaddrinfo."""
    found: dict[str, dict] = {}
    for family, *_rest, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            found[str(ip)] = {"ip": str(ip), "interface": None}
    return list(found.values())


class LTMSError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class LTMSClient:
    def __init__(self, base_url: str | None = None, *, key_path: str | None = None, name: str | None = None,
                 signing_key=None, http: httpx.Client | None = None, timeout: float = 15.0,
                 state_path: str | None = None,
                 on_memory_wipe: Callable[[int], None] | None = None) -> None:
        if key_path:
            name, signing_key = load_key(key_path)
        if signing_key is None or not name:
            raise ValueError("need key_path, or name and signing_key")
        self.name, self.key = name, signing_key
        self.http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self.token: str | None = None
        self.token_expires: datetime | None = None
        self.permissions: list[str] = []
        self.seq = 0                 # last seq the server acknowledged
        self.last_hash = GENESIS_HASH
        self.outbox: list[dict] = []  # signed, not yet acknowledged
        self.state_path = state_path   # small JSON file remembering the last seen memory_generation
        self.on_memory_wipe = on_memory_wipe  # device-specific callback; never set by this package

    # ------------------------------------------------------------------ generation tracking

    def _load_generation(self) -> int | None:
        """Last seen memory_generation, or None if not known yet."""
        if self.state_path is None:
            return None
        try:
            with open(self.state_path) as f:
                state = json.load(f)
        except (FileNotFoundError, ValueError):  # no file, or not JSON
            return None
        # Anything but {"memory_generation": <int>} is "not known yet". A bool is
        # an int in Python, so it is ruled out by name.
        value = state.get("memory_generation") if isinstance(state, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _save_generation(self, value: int) -> None:
        if self.state_path is None:
            return
        # Write a temp file and swap it in, so a crash mid-save leaves the old file intact.
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"memory_generation": value}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def _check_generation(self, value: int) -> None:
        """Warn the device when the server's memory_generation moved ahead of what we last saw.

        A wipe resets memory, not the chain: this never touches self.outbox.
        The new value is saved only after the callback returns, so a crash re-runs it.
        """
        saved = self._load_generation()
        if saved is None:
            self._save_generation(value)   # not known yet: remember it, no callback
            return
        if value > saved:
            if self.on_memory_wipe is not None:
                self.on_memory_wipe(value)
            self._save_generation(value)   # also with no callback, so one added later doesn't fire for an old wipe

    # ------------------------------------------------------------------ http
    def _call(self, method: str, path: str, *, auth: bool = True, **kw) -> dict:
        headers = kw.pop("headers", {})
        if auth:
            self._ensure_token()
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self.http.request(method, path, headers=headers, **kw)
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
            except Exception:
                err = {"code": "http_error", "message": resp.text[:200]}
            raise LTMSError(resp.status_code, err["code"], err["message"])
        return resp.json()

    # ------------------------------------------------------------------ enroll / login
    def enroll(self, kind: str, permissions: list[str], operator: str | None = None, note: str | None = None,
               intended: dict | None = None) -> dict:
        """``intended`` = ``{"swarm", "role"}``: what this member is for (#295). A request, never a grant."""
        body = {"name": self.name, "kind": kind, "public_key": public_key_hex(self.key),
                "permissions": permissions, "operator": operator, "note": note}
        if intended:
            body["intended"] = intended
        return self._call("POST", "/api/auth/enroll", auth=False, json=body)

    def login(self) -> dict:
        ch = self._call("POST", "/api/auth/challenge", auth=False, json={"name": self.name})
        sig = self.key.sign(auth_signing_message(ch["nonce"])).signature.hex()
        tok = self._call("POST", "/api/auth/token", auth=False,
                         json={"name": self.name, "nonce": ch["nonce"], "sig": sig})
        self.token = tok["token"]
        self.token_expires = datetime.fromisoformat(tok["expires_at"])
        self.permissions = tok["permissions"]
        self.seq, self.last_hash = tok["last_ack_seq"], tok["last_hash"]
        self._check_generation(tok["memory_generation"])
        self._maybe_read_charter(tok.get("charter"))
        return tok

    # ------------------------------------------------------------------ charter (#255)
    auto_read_charter = True

    def read_charter(self) -> dict | None:
        """Read the fleet charter (logged by the server). Never raises; None on failure."""
        try:
            self.charter = self._call("GET", "/api/charter")
            return self.charter
        except Exception as exc:  # noqa: BLE001 - reading is best effort; writes say if it's needed
            log.warning("read_charter failed: %s", exc)
            return None

    def _maybe_read_charter(self, status: dict | None) -> None:
        if self.auto_read_charter and isinstance(status, dict) and status.get("read") is False:
            self.read_charter()

    def _ensure_token(self) -> None:
        if self.token is None or self.token_expires is None or \
                self.token_expires - datetime.now(timezone.utc) < timedelta(seconds=20):
            self.login()

    def sync_head(self) -> dict:
        head = self._call("GET", "/api/records/head")
        self.seq, self.last_hash = head["last_ack_seq"], head["last_hash"]
        self.outbox.clear()
        self._check_generation(head["memory_generation"])
        self._maybe_read_charter(head.get("charter"))
        return head

    def report_cognition(self, location: str, models: list[dict] | None = None) -> dict | None:
        """Tell LTMS where this device's AI runs: "onboard" or "offboard" (#185).

        ``models`` is up to four ``{"name", "runs_on"}``. Never raises: a robot
        shouldn't stop because a report failed. Returns the resolved value, or None.
        """
        try:
            return self._call("PUT", "/api/members/me/cognition",
                              json={"location": location, "models": models or []})["cognition"]
        except Exception as exc:  # noqa: BLE001 - reporting is best effort
            log.warning("report_cognition failed: %s", exc)
            return None

    def report_network(self, addresses: list[dict] | None = None) -> list[dict] | None:
        """Tell LTMS this device's LAN addresses (#190); LTMS labels them unverified.

        ``addresses`` is up to eight ``{"ip", "interface"}``. With None, collect
        this host's non-loopback IPv4 and IPv6 addresses (standard library only).
        Never raises: returns what LTMS stored, or None after logging the error.
        """
        try:
            if addresses is None:
                addresses = local_addresses()
            return self._call("PUT", "/api/members/me/network", json={"addresses": addresses[:8]})["addresses"]
        except Exception as exc:  # noqa: BLE001 - reporting is best effort
            log.warning("report_network failed: %s", exc)
            return None

    # ------------------------------------------------------------------ write
    def append(self, type: str, *, payload: dict | None = None, ts: str | None = None, **header) -> dict:
        """Sign the next record locally and queue it. Works offline."""
        prev_seq = self.outbox[-1]["seq"] if self.outbox else self.seq
        prev_hash = self.outbox[-1]["hash"] if self.outbox else self.last_hash
        rec = build_record(self.key, agent=self.name, seq=prev_seq + 1, prev_hash=prev_hash,
                           type=type, payload=payload, ts=ts, **header)
        self.outbox.append(rec)
        return rec

    def flush(self, batch_size: int = 100) -> list[dict]:
        """Send queued records. Stops at the first rejection and raises."""
        responses = []
        charter_retried = False
        while self.outbox:
            batch = self.outbox[:batch_size]
            res = self._call("POST", "/api/records", json={"records": batch})
            responses.append(res)
            self.seq, self.last_hash = res["last_ack_seq"], res["last_hash"]
            self.outbox = [r for r in self.outbox if r["seq"] > self.seq]
            if not res["ok"]:
                bad = next(r for r in res["results"] if r["status"] == "rejected")
                if bad["code"] == "charter_unread" and self.auto_read_charter and not charter_retried:
                    # The charter changed (or was never read): read it, then send again (#255).
                    charter_retried = True
                    if self.read_charter() is not None:
                        continue
                raise LTMSError(422, bad["code"], f"seq {bad['seq']}: {bad['message']}")
        return responses

    # ------------------------------------------------------------------ relay
    def relay(self, records: list[dict]) -> dict:
        """Submit records signed by *other* members of this hub's swarm, unchanged.

        Needs the hub role (and so the ``relay`` permission). The response's
        ``heads`` gives each author's chain head after the batch; like a member's
        own flush, it stops at the first rejection (see ``results``).
        """
        return self._call("POST", "/api/records", json={"records": records})

    def head_of(self, agent: str) -> dict:
        """Another member's chain head (hub of that member's swarm only)."""
        return self._call("GET", "/api/records/head", params={"agent": agent})

    # ------------------------------------------------------------------ read
    def records(self, **params) -> list[dict]:
        if "from_" in params:
            params["from"] = params.pop("from_")
        return self._call("GET", "/api/records", params={k: v for k, v in params.items() if v is not None})["records"]

    def context(self, intent: str | None = None, target: str | None = None, place: str | None = None) -> dict:
        params = {k: v for k, v in {"intent": intent, "target": target, "place": place}.items() if v}
        return self._call("GET", "/api/recall/context", params=params)


    # ------------------------------------------------------------------ datasets (#238, #240)
    def datasets(self) -> list[dict]:
        """The fleet's approved datasets (reference material)."""
        return self._call("GET", "/api/datasets")["datasets"]

    def search_dataset(self, dataset_id: str, q: str, *, version: int | None = None, limit: int = 20) -> list[dict]:
        params = {"q": q, "limit": limit}
        if version is not None:
            params["version"] = version
        return self._call("GET", f"/api/datasets/{dataset_id}/search", params=params)["hits"]

    def read_item(self, dataset_id: str, key: str, *, version: int | None = None, cycle: str | None = None) -> dict:
        """Read one item; the server logs the read (and sends it to the LRS)."""
        params = {k: v for k, v in {"version": version, "cycle": cycle}.items() if v is not None}
        return self._call("GET", f"/api/datasets/{dataset_id}/items/{quote(key, safe='/')}", params=params)

    def report_read(self, dataset_id: str, version: int, item: str, *, cycle: str | None = None) -> dict:
        """Report an item this member read itself, e.g. an external one fetched from its source (#248)."""
        body = {"dataset": dataset_id, "version": version, "item": item}
        if cycle is not None:
            body["cycle"] = cycle
        return self._call("POST", "/api/members/me/reads", json=body)

    def dream_cycle(self, *, jitter_s: float = 900, sleep: Callable[[float], None] = time.sleep) -> "DreamCycle":
        """``with client.dream_cycle() as cycle:`` - a reading-and-reflection cycle."""
        return DreamCycle(self, jitter_s=jitter_s, sleep=sleep)


class DreamCycle:
    """A dream cycle (#240): start event, reads and lessons, end event.

    Entering waits a random 0..``jitter_s`` seconds (so a fleet doesn't read in
    lockstep), then queues the start ``event``. ``read`` reads with the cycle id;
    ``lesson`` queues a lesson citing its sources. Leaving always queues the end
    event with the counts, even if the body raised, and flushes. Records are
    appended locally, so a cycle works offline until the flush.
    """

    TAG = "dream_cycle"

    def __init__(self, client: LTMSClient, *, jitter_s: float = 900,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self.jitter_s = jitter_s
        self._sleep = sleep
        self.id = str(uuid.uuid4())
        self.items_read = 0
        self.lessons = 0

    def __enter__(self) -> "DreamCycle":
        if self.jitter_s and self.jitter_s > 0:
            self._sleep(random.uniform(0, self.jitter_s))
        self.client.append("event", summary="Dream cycle started", tags=[self.TAG],
                           payload={"cycle": self.id, "phase": "start"})
        return self

    def read(self, dataset_id: str, key: str, *, version: int | None = None) -> dict:
        item = self.client.read_item(dataset_id, key, version=version, cycle=self.id)
        self.items_read += 1
        return item

    def report(self, dataset_id: str, version: int, item: str) -> dict:
        """Report an external item read during this cycle (fetched from its source, #248)."""
        res = self.client.report_read(dataset_id, version, item, cycle=self.id)
        self.items_read += 1
        return res

    def lesson(self, summary: str, *, sources: list[dict] | None = None, refs: dict | None = None,
               payload: dict | None = None, **header) -> dict:
        """Queue a lesson. ``sources``: up to 16 ``{"dataset", "version", "item"}``."""
        body = dict(payload or {})
        body["cycle"] = self.id
        if sources:
            body["sources"] = sources
        if refs is not None:
            header["refs"] = refs
        rec = self.client.append("lesson", summary=summary, payload=body, **header)
        self.lessons += 1
        return rec

    def __exit__(self, exc_type, exc, tb) -> bool:
        payload = {"cycle": self.id, "phase": "end", "items_read": self.items_read, "lessons": self.lessons}
        if exc_type is not None:
            payload["error"] = exc_type.__name__
        self.client.append("event", summary="Dream cycle ended", tags=[self.TAG], payload=payload)
        try:
            self.client.flush()
        except Exception as flush_exc:  # noqa: BLE001 - records stay queued in the outbox
            log.warning("dream cycle flush failed; records stay queued: %s", flush_exc)
        return False
