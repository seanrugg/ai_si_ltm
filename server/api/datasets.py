"""Member access to fleet datasets (#238, docs/datasets-design.md).

Any active member may read any approved dataset (``scope: fleet``). Every item
read is logged; searches are not.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from server.api.deps import MemberAuth, current_member, transaction
from server.services import datasets as datasets_service

router = APIRouter(prefix="/api", tags=["datasets"])
MEMBER = Depends(current_member)
_RANGE_START = re.compile(r"^\s*bytes=(\d*)-")


@router.get("/datasets")
async def list_datasets(request: Request, auth: MemberAuth = MEMBER) -> dict:
    async with transaction(request) as session:
        return {"datasets": await datasets_service.list_for_members(session)}


@router.get("/datasets/{dataset_id}/search")
async def search_dataset(dataset_id: str, request: Request, q: str = "", version: int | None = None,
                         limit: int = 20, auth: MemberAuth = MEMBER) -> dict:
    async with transaction(request) as session:
        return await datasets_service.search(session, dataset_id, q, version=version, limit=limit)


@router.get("/datasets/{dataset_id}/items/{key:path}/content")
async def item_content(dataset_id: str, key: str, request: Request, version: int | None = None,
                       cycle: str | None = None, auth: MemberAuth = MEMBER):
    """The item's stored file (#245): Range, ETag and caching. One read per file, not per range."""
    m = _RANGE_START.match(request.headers.get("range", ""))
    async with transaction(request) as session:
        path, item = await datasets_service.item_content(
            session, request.app.state.config, member_id=auth.member_id, dataset_id=dataset_id, key=key,
            version=version, cycle=cycle, range_start=int(m.group(1)) if m else None)
    return FileResponse(path, media_type=item.media_type, filename=item.key.rsplit("/", 1)[-1],
                        content_disposition_type="attachment", headers={
                            "ETag": f'"{item.sha256}"',
                            # Versions are immutable, so a member may cache an item for a day.
                            "Cache-Control": "private, max-age=86400, immutable",
                            "X-Content-Type-Options": "nosniff",
                            "Content-Security-Policy": "sandbox",
                        })


@router.get("/datasets/{dataset_id}/items/{key:path}")
async def read_item(dataset_id: str, key: str, request: Request, version: int | None = None,
                    cycle: str | None = None, auth: MemberAuth = MEMBER) -> dict:
    """Read one item. Logged as a dataset read (and sent to the LRS, #239)."""
    async with transaction(request) as session:
        return await datasets_service.read_item(session, member_id=auth.member_id, dataset_id=dataset_id,
                                                key=key, version=version, cycle=cycle)
