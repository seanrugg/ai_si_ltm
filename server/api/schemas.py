"""Pydantic request/response models for the API surface.

These define the wire contract for the public, member, and admin endpoints.
Request models are validated before any handler runs; response models
(omitted for brevity in most admin endpoints) are documented inline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo


# --------------------------------------------------------------------------- #
# Public: enrollment + challenge
# --------------------------------------------------------------------------- #
class EnrollRequest(BaseModel):
    """Submit an enrollment request (public, per-IP capped)."""

    name: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(..., description="robot | agent | assistant | relay")
    operator: Optional[str] = Field(None, max_length=256)


class ChallengeResponse(BaseModel):
    """A one-time nonce + replay window (public, per-IP capped)."""

    nonce: str
    expires_at: datetime


# --------------------------------------------------------------------------- #
# Member: token exchange
# --------------------------------------------------------------------------- #
class TokenRequest(BaseModel):
    """Exchange a fresh /api/auth/challenge nonce + signature for a token."""

    nonce: str = Field(..., description="nonce from /api/auth/challenge")
    signature: str = Field(..., description="Ed25519 sig over ltms-auth-v1 : nonce")


class TokenResponse(BaseModel):
    token: str
    expires_at: datetime


# --------------------------------------------------------------------------- #
# Member: ingest
# --------------------------------------------------------------------------- #
class RecordIn(BaseModel):
    """One record to ingest."""

    seq: int = Field(..., ge=1)
    ts: str = Field(..., description="ISO-8601 UTC timestamp")
    type: str
    agent: str
    body_hash: str = Field(..., description="64 hex chars")
    sig: str = Field(..., description="64 hex chars")
    hash: str = Field(..., description="64 hex chars")
    prev_hash: str = Field(..., description="64 hex chars")
    summary: Optional[str] = None
    refs: dict = Field(default_factory=dict)
    place: Optional[str] = None
    peers: list = Field(default_factory=list)
    status: Optional[str] = None
    body: dict = Field(default_factory=dict)
    media_type: list = Field(default_factory=list)


class RecordBatch(BaseModel):
    """A batch of up to max_batch records."""

    records: list[RecordIn] = Field(..., max_items=100)


class RecordOutcome(BaseModel):
    """Per-record outcome of an ingest."""

    seq: int
    status: str
    code: Optional[str] = None
    message: Optional[str] = None


class BatchResponse(BaseModel):
    outcomes: list[RecordOutcome]


# --------------------------------------------------------------------------- #
# Member: recall
# --------------------------------------------------------------------------- #
class RecallQuery(BaseModel):
    """Query params for GET /api/records."""

    type: Optional[str] = None
    agent: Optional[str] = None
    place: Optional[str] = None
    from_: Optional[datetime] = Field(None, alias="from")
    to: Optional[datetime] = None
    limit: int = Field(25, ge=1, le=200)


class RecallContextQuery(BaseModel):
    """Query params for GET /api/recall/context."""

    intent: Optional[str] = None
    target: Optional[str] = None
    place: Optional[str] = None


# --------------------------------------------------------------------------- #
# Admin: login
# --------------------------------------------------------------------------- #
class AdminLogin(BaseModel):
    username: str
    password: str


# --------------------------------------------------------------------------- #
# Admin: enrollment actions
# --------------------------------------------------------------------------- #
class SetPermissions(BaseModel):
    permissions: list[str]


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    operator: Optional[str] = None
    permissions: list
    status: str
    enrolled_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_ack_seq: int = 0
