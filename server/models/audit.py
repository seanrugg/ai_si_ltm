"""Audit log: recalls, logins, enrollment decisions, rejected ingests, purges."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, BigIntPK, JSONType, UTCDateTime, utcnow

AUDIT_ACTIONS = (
    "recall",
    "admin_login",
    "admin_login_failed",
    "enroll_request",
    "enroll_approve",
    "enroll_reject",
    "member_revoke",
    "permissions_change",
    "token_issued",
    "token_denied",
    "ingest_reject",
    "retention_purge",
    "record_export",
    "lrs_create",
    "lrs_update",
    "lrs_delete",
    "lrs_skip",
    "lrs_resume",
    "mcp_token_create",
    "mcp_token_revoke",
    "mcp_settings_change",
    "mcp_settings_reset",
    "retention_change",
    "record_delete",
    "memory_wipe",
    "cognition_change",
    "network_change",
    "location_change",
    "swarm_rename",
    "swarm_remove",
    "records_purge",
    "dataset_create",
    "dataset_version",
    "dataset_approve",
    "dataset_retire",
    "dataset_upload_expire",
    "integration_create",
    "integration_delete",
)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_ts", "ts"),
        Index("idx_audit_member_ts", "member_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    member_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("members.id"))
    actor: Mapped[str | None] = mapped_column(String(64))  # member name or "admin:<user>"
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    response_count: Mapped[int | None] = mapped_column(Integer)
