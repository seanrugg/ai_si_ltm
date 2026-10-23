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

import httpx

from ltms_contract import GENESIS_HASH, auth_signing_message, build_record, load_key, public_key_hex


class LTMSError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class LTMSClient:
    def __init__(self, base_url: str | None = None, *, key_path: str | None = None, name: str | None = None,
                 signing_key=None, http: httpx.Client | None = None, timeout: float = 15.0) -> None:
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
    def enroll(self, kind: str, permissions: list[str], operator: str | None = None, note: str | None = None) -> dict:
        return self._call("POST", "/api/auth/enroll", auth=False, json={
            "name": self.name, "kind": kind, "public_key": public_key_hex(self.key),
            "permissions": permissions, "operator": operator, "note": note,
        })

    def login(self) -> dict:
        ch = self._call("POST", "/api/auth/challenge", auth=False, json={"name": self.name})
        sig = self.key.sign(auth_signing_message(ch["nonce"])).signature.hex()
        tok = self._call("POST", "/api/auth/token", auth=False,
                         json={"name": self.name, "nonce": ch["nonce"], "sig": sig})
        self.token = tok["token"]
        self.token_expires = datetime.fromisoformat(tok["expires_at"])
        self.permissions = tok["permissions"]
        self.seq, self.last_hash = tok["last_ack_seq"], tok["last_hash"]
        return tok

    def _ensure_token(self) -> None:
        if self.token is None or self.token_expires is None or \
                self.token_expires - datetime.now(timezone.utc) < timedelta(seconds=20):
            self.login()

    def sync_head(self) -> dict:
        head = self._call("GET", "/api/records/head")
        self.seq, self.last_hash = head["last_ack_seq"], head["last_hash"]
        self.outbox.clear()
        return head

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
        while self.outbox:
            batch = self.outbox[:batch_size]
            res = self._call("POST", "/api/records", json={"records": batch})
            responses.append(res)
            self.seq, self.last_hash = res["last_ack_seq"], res["last_hash"]
            self.outbox = [r for r in self.outbox if r["seq"] > self.seq]
            if not res["ok"]:
                bad = next(r for r in res["results"] if r["status"] == "rejected")
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
