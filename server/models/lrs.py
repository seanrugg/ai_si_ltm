"""LRS endpoints: admin-configured xAPI endpoints (issue #59).

Grow-by-adding-tables: this adds ``lrs_endpoints`` and nothing else. LTMS works
with no endpoint configured; forwarding and statement translation are separate
issues.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base, BigIntPK, JSONType, UTCDateTime, utcnow

XAPI_VERSIONS = ("2.0.0", "1.0.3")
AUTH_TYPES = ("basic",)


class LrsEndpoint(Base):
    """One admin-configured Learning Record Store endpoint.

    ``secret_enc`` holds a Fernet-encrypted shared secret/basic password; it is
    never returned by any API and never logged.
    """

    __tablename__ = "lrs_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)  # trailing "/" stripped
    xapi_version: Mapped[str] = mapped_column(String(8), nullable=False, default="2.0.0")
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    scope: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Last Record.id forwarded (the cursor for the forwarder, issue #60).
    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
