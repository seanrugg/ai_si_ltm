"""Ed25519 key files for members.

A key file is JSON: ``{"name": ..., "secret_key": <64 hex>, "public_key": <64 hex>}``.
It is written with mode 0600. Keep it off shared storage and out of git.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from nacl.signing import SigningKey


def generate_key() -> SigningKey:
    return SigningKey.generate()


def public_key_hex(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


def save_key(path: str | Path, name: str, key: SigningKey) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "secret_key": bytes(key).hex(), "public_key": public_key_hex(key)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)


def load_key(path: str | Path) -> tuple[str, SigningKey]:
    data = json.loads(Path(path).read_text())
    key = SigningKey(bytes.fromhex(data["secret_key"]))
    if public_key_hex(key) != data.get("public_key", public_key_hex(key)):
        raise ValueError(f"{path}: public_key does not match secret_key")
    return data["name"], key
