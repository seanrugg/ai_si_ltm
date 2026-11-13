"""The I2IDL Glossary of Digital Learning as fleet items (#253, #351). Pure: no I/O.

Used by the dashboard's reference-source import (server side) and by
``scripts/import_glossary.py`` (command line). Keep this module free of server
imports so the script stays light.
"""
from __future__ import annotations

GLOSSARY_URL = "https://id.i2idl.org/glossary.jsonld"
DATASET = "i2idl-glossary"


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
