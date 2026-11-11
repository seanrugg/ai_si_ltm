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

GLOSSARY_URL = "https://id.i2idl.org/glossary.jsonld"
DATASET = "i2idl-glossary"
BATCH = 50


def _list(v) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _ids(v) -> list[str]:
    return [x["@id"] if isinstance(x, dict) else str(x) for x in _list(v)]


def _types(node) -> list[str]:
    return _list(node.get("@type"))


def build(doc: dict) -> tuple[dict, list[dict]]:
    """``(publication, items)`` from the glossary JSON-LD. Pure: no I/O."""
    graph = doc.get("@graph") or []
    by_id = {n.get("@id"): n for n in graph}
    scheme = next((n for n in graph if "skos:ConceptScheme" in _types(n)), {})
    version = scheme.get("gs:publicationVersion")
    if not version:
        raise ValueError("no gs:publicationVersion in the glossary scheme")
    labels = {n["@id"]: n.get("skos:prefLabel") for n in graph if "skos:Collection" in _types(n)}
    pub = {
        "title": scheme.get("dcterms:title", "I2IDL Glossary of Digital Learning"),
        "publicationVersion": version,
        "rights": scheme.get("dcterms:rights"),
        "source": GLOSSARY_URL,
        "scheme": scheme.get("@id"),
    }
    items = []
    for n in graph:
        if "skos:Concept" not in _types(n):
            continue
        cid = n.get("dcterms:identifier") or n["@id"].rsplit("/", 1)[-1]
        definition = by_id.get(_ids(n.get("gs:activeDefinition"))[0], {}) if n.get("gs:activeDefinition") else {}
        refs = []
        for ev in _list(definition.get("gs:evidence")):
            src_id = _ids(ev.get("dcterms:source"))[0] if ev.get("dcterms:source") else None
            src = by_id.get(src_id, {})
            refs.append({"source": ev.get("gs:sourceLabel") or src.get("dcterms:title"),
                         "title": src.get("dcterms:title"), "detail": ev.get("gs:citationDetail"),
                         "url": ev.get("schema:url"), "relation": ev.get("gs:evidenceRelation"),
                         "rights": src.get("dcterms:rights"), "rights_url": src.get("gs:rightsUrl")})
        items.append({
            "id": cid,
            "uri": n["@id"],
            "term": n.get("skos:prefLabel"),
            "aka": [a for a in _list(n.get("skos:altLabel")) if a],
            "definition": n.get("skos:definition") or definition.get("rdf:value"),
            "why": n.get("gs:editorialExplanation"),
            "field": labels.get(_ids(n.get("gs:fieldCollection"))[0]) if n.get("gs:fieldCollection") else None,
            "type": labels.get(_ids(n.get("gs:typeCollection"))[0]) if n.get("gs:typeCollection") else None,
            "references": refs,
            "relationships": {k.split(":")[1]: _ids(n.get(k)) for k in
                              ("skos:broader", "skos:narrower", "skos:related", "skos:exactMatch",
                               "skos:closeMatch", "skos:relatedMatch") if n.get(k)},
            "rights": "I2IDL-original material: CC BY 4.0; each reference keeps its source's rights",
            "attribution": f"{pub['title']} ({version}), {n['@id']}",
            "publicationVersion": version,
        })
    items.sort(key=lambda i: i["id"])
    return pub, items


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
