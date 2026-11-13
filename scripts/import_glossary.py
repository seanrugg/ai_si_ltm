"""Import the I2IDL Glossary of Digital Learning as a fleet dataset (#253).

    python -m scripts.import_glossary --server https://ltms.example.org
    python -m scripts.import_glossary --server ... --from-file glossary.jsonld   # closed networks

Reads the published JSON-LD (https://id.i2idl.org/glossary.jsonld by default),
writes one JSON item per concept, ``concepts/<id>.json``, into a pending
version of the dataset ``i2idl-glossary``, and stops: an admin approves the
version like any other. One version per glossary publication
(``gs:publicationVersion``); re-running for a publication already imported
changes nothing.

Rights travel with the data. The scheme's own statement ("Mixed rights.
I2IDL-original material defaults to CC BY 4.0; source-specific rights are
preserved at the source and evidence level") is the dataset licence, and each
item lists its definition's sources with their rights (CC BY, CC BY-SA,
CC BY-NC, Apache 2.0, public domain, used with permission, IEEE terms).
LTMS serves them as reference reading inside the fleet, unmodified and
attributed.

Admin credentials come from LTMS_ADMIN_USERNAME and LTMS_ADMIN_PASSWORD.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from server.services.i2idl_glossary import DATASET, GLOSSARY_URL, build  # noqa: F401

BATCH = 50


def _ok(r, what):
    if r.status_code >= 400:
        raise SystemExit(f"{what}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def import_glossary(admin, doc: dict, *, log=print) -> dict:
    """Import into LTMS through ``admin`` (an authenticated client with get/post).

    Returns ``{"status": "imported"|"unchanged", "version", "publicationVersion", "items"}``.
    """
    pub, items = build(doc)
    marker = f"publication/{pub['publicationVersion']}.json"
    found = next((d for d in _ok(admin.get("/api/admin/datasets"), "list datasets")["datasets"]
                  if d["name"] == DATASET), None)
    if found is None:
        found = _ok(admin.post("/api/admin/datasets", json={
            "name": DATASET, "kind": "url", "licence": pub["rights"] or "Mixed rights; see each item",
            "description": f"{pub['title']}, publication {pub['publicationVersion']}, from {GLOSSARY_URL}. "
                           "Reference reading; cite concepts by id."}), "create dataset")
    did = found["id"]
    for v in found["versions"]:
        keys = {i["key"] for i in _ok(admin.get(f"/api/admin/datasets/{did}/versions/{v['version']}/items"),
                                      "list items")["items"]}
        if marker in keys:
            log(f"{pub['publicationVersion']} is already imported as version {v['version']}; nothing to do")
            return {"status": "unchanged", "version": v["version"], "publicationVersion": pub["publicationVersion"],
                    "items": len(items)}
    latest = found["versions"][-1] if found["versions"] else None
    if latest and latest["status"] == "pending":
        if latest["item_count"]:
            raise SystemExit(f"version {latest['version']} is pending with items; approve or retire it first")
        version = latest["version"]
    else:
        version = _ok(admin.post(f"/api/admin/datasets/{did}/versions"), "new version")["version"]
    url = f"/api/admin/datasets/{did}/versions/{version}/items"
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        files = [("files", (f"{c['id']}.json", json.dumps(c, ensure_ascii=False, indent=1).encode("utf-8"),
                            "application/json")) for c in batch]
        _ok(admin.post(url, files=files, data={"prefix": "concepts"}), f"upload concepts {start}-{start + len(batch)}")
        log(f"uploaded {min(start + BATCH, len(items))}/{len(items)}")
    _ok(admin.post(url, files=[("files", (f"{pub['publicationVersion']}.json",
                                          json.dumps({**pub, "concepts": len(items)}).encode("utf-8"),
                                          "application/json"))], data={"prefix": "publication"}), "publication record")
    log(f"imported {len(items)} concepts as version {version} (pending): approve it on the Datasets page")
    return {"status": "imported", "version": version, "publicationVersion": pub["publicationVersion"],
            "items": len(items)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server", required=True, help="LTMS base URL")
    ap.add_argument("--from-file", help="a saved glossary.jsonld (no network needed for the glossary)")
    ap.add_argument("--url", default=GLOSSARY_URL, help="where to fetch the JSON-LD")
    args = ap.parse_args(argv)
    if args.from_file:
        with open(args.from_file, encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = httpx.get(args.url, timeout=60, follow_redirects=True).raise_for_status().json()
    user, password = os.environ.get("LTMS_ADMIN_USERNAME"), os.environ.get("LTMS_ADMIN_PASSWORD")
    if not user or not password:
        print("set LTMS_ADMIN_USERNAME and LTMS_ADMIN_PASSWORD", file=sys.stderr)
        return 2
    with httpx.Client(base_url=args.server.rstrip("/"), headers={"X-LTMS-Admin": "1"}, timeout=120) as admin:
        _ok(admin.post("/api/auth/admin/login", json={"username": user, "password": password}), "admin login")
        import_glossary(admin, doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
