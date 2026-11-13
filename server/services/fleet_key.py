"""The fleet key (#303): an Ed25519 key LTMS holds to sign directives, so members can
prove a directive came from this fleet. Created on first use (file mode 0600) and
reused after; never logged. Only the public half leaves the server.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey

_lock = threading.Lock()
_cache: dict[str, SigningKey] = {}


def path_for(cfg) -> Path:
    return Path(getattr(cfg, "fleet_key_path", None) or os.path.join(cfg.data_dir, "fleet-key.json"))


def load(cfg) -> SigningKey:
    path = path_for(cfg)
    key_id = str(path.resolve())
    with _lock:
        if key_id in _cache:
            return _cache[key_id]
        if path.exists():
            key = SigningKey(bytes.fromhex(json.loads(path.read_text())["secret_key"]))
        else:
            key = SigningKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"secret_key": bytes(key).hex(), "public_key": bytes(key.verify_key).hex()}, fh)
        _cache[key_id] = key
        return key


def public_hex(cfg) -> str:
    return bytes(load(cfg).verify_key).hex()


def sign(cfg, domain: str, statement_bytes: bytes) -> str:
    return load(cfg).sign(domain.encode("utf-8") + statement_bytes).signature.hex()


def verify(public_key_hex: str, domain: str, statement_bytes: bytes, sig_hex: str) -> bool:
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(domain.encode("utf-8") + statement_bytes, bytes.fromhex(sig_hex))
        return True
    except Exception:  # noqa: BLE001 - any failure is "not valid"
        return False
