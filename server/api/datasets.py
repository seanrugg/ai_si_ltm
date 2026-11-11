"""Member access to fleet datasets (#238, docs/datasets-design.md).

Any active member may read any approved dataset (``scope: fleet``). Every item
read is logged; searches are not.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from server.api.deps import MemberAuth, current_member, transaction
from server.services import datasets as datasets_service

router = APIRouter(prefix="/api", tags=["datasets"])
MEMBER = Depends(current_member)


@router.get("/datasets")
async def list_datasets(request: Request, auth: MemberAuth = MEMBER) -> dict:
    async with transaction(request) as session:
        return {"datasets": await datasets_service.list_for_members(session)}


@router.get("/datasets/{dataset_id}/search")
async def search_dataset(dataset_id: str, request: Request, q: str = "", version: int | None = None,
                         limit: int = 20, auth: MemberAuth = MEMBER) -> dict:
    async with transaction(request) as session:
        return await datasets_service.search(session, dataset_id, q, version=version, limit=limit)


@router.get("/datasets/{dataset_id}/items/{key:path}")
async def read_item(dataset_id: str, key: str, request: Request, version: int | None = None,
                    cycle: str | None = None, auth: MemberAuth = MEMBER) -> dict:
    """Read one item. Logged as a dataset read (and sent to the LRS, #239)."""
    async with transaction(request) as session:
        return await datasets_service.read_item(session, member_id=auth.member_id, dataset_id=dataset_id,
                                                key=key, version=version, cycle=cycle)
