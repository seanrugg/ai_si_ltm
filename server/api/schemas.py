"""Request bodies. Records themselves are plain JSON objects validated by
``ltms_contract`` (pydantic would re-serialize them and break hashing)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EnrollRequest(BaseModel):
    name: str = Field(..., max_length=64)
    kind: str
    public_key: str = Field(..., description="Ed25519 public key, 64 hex chars")
    permissions: list[str] = Field(default_factory=lambda: ["contribute", "recall"])
    operator: str | None = Field(None, max_length=128)
    note: str | None = Field(None, max_length=1000)
    swarm: str | None = Field(None, max_length=64)


class ChallengeRequest(BaseModel):
    name: str = Field(..., max_length=64)


class TokenRequest(BaseModel):
    name: str = Field(..., max_length=64)
    nonce: str = Field(..., max_length=64)
    sig: str = Field(..., max_length=128)


class RecordBatch(BaseModel):
    records: list[dict] = Field(..., min_length=1)


class AdminLogin(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=256)


class ApproveRequest(BaseModel):
    permissions: list[str] | None = None  # default: what the member requested
    scope: dict | None = None
    join_swarm: bool = True


class ReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PermissionsRequest(BaseModel):
    permissions: list[str]
    scope: dict | None = None
