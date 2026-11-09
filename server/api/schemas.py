"""Request bodies. Records themselves are plain JSON objects validated by
``ltms_contract`` (pydantic would re-serialize them and break hashing)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EnrollRequest(BaseModel):
    name: str = Field(..., max_length=64)
    kind: str
    public_key: str = Field(..., description="Ed25519 public key, 64 hex chars")
    permissions: list[str] = Field(default_factory=lambda: ["contribute", "recall"])
    operator: str | None = Field(None, max_length=128)
    note: str | None = Field(None, max_length=1000)
    swarm: str | None = Field(None, max_length=64)
    invite: str | None = Field(None, max_length=128)


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


class McpTokenCreate(BaseModel):
    name: str = Field(..., max_length=64)
    scope: dict | None = Field(default_factory=dict)
    expires_at: datetime | None = None
    owner: str | None = Field(None, max_length=128)
    purpose: str | None = Field(None, max_length=500)


class InviteCreate(BaseModel):
    uses: int = Field(..., ge=1, le=10000)
    expires_at: datetime | None = None


class SwarmMode(BaseModel):
    mode: str  # "approval", "invite" or "open"
    open_permissions: list[str] | None = None


class McpSettingsUpdate(BaseModel):
    mode: str  # "off", "token" or "open"
    open_scope: dict | None = None
    confirm: str | None = None


class EnrollmentPolicyUpdate(BaseModel):
    policy: str  # "manual", "auto_deny" or "auto_approve"
    confirm: str | None = None


class BulkMembersRequest(BaseModel):
    action: str  # "approve" or "reject"
    ids: list[str] = Field(..., max_length=100)
    reason: str | None = Field(None, max_length=500)
