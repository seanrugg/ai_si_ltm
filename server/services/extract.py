"""Media-type sniffing and text extraction for dataset files (#237).

The type comes from the **content**, with the file name only choosing between
text formats that look alike (csv vs tsv vs plain text). Text-tier formats:
txt, md, html, csv, tsv, json, jsonl, yaml, xml. Standard library plus PyYAML.
"""
from __future__ import annotations

import csv
import io
import json
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree

import yaml

TEXT_CAP = 1024 * 1024  # characters kept in dataset_items.text
SAMPLE_ROWS = 5

# Binary signatures: anything matching is not text, whatever its name says.
MAGIC = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"RIFF", "audio/wav"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"BZh", "application/x-bzip2"),
    (b"\x7fELF", "application/x-executable"),
    (b"MZ", "application/x-msdownload"),
    (b"\x89HDF", "application/x-hdf5"),
    (b"\x93NUMPY", "application/x-npy"),
    (b"\x89MCAP", "application/x-mcap"),
    (b"#ROSBAG", "application/x-rosbag"),
    (b"fLaC", "audio/flac"),
    (b"ID3", "audio/mpeg"),
    (b"\x1aE\xdf\xa3", "video/x-matroska"),
    (b"PAR1", "application/x-parquet"),
]

TEXT_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".html": "text/html", ".htm": "text/html", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".jsonl": "application/x-ndjson", ".ndjson": "application/x-ndjson",
    ".yaml": "application/yaml", ".yml": "application/yaml", ".xml": "application/xml",
}
TEXT_TIER = set(TEXT_TYPES.values())


def _looks_binary(head: bytes) -> bool:
    if b"\x00" in head[:4096]:
        return True
    try:
        head[:4096].decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        return exc.start < len(head[:4096]) - 4  # a cut multi-byte char at the end is fine


def sniff(head: bytes, filename: str) -> str:
    """The media type, from the content first."""
    for sig, mtype in MAGIC:
        if head.startswith(sig):
            if mtype == "audio/wav" and head[8:12] != b"WAVE":
                return "application/octet-stream"
            return mtype
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if _looks_binary(head):
        return "application/octet-stream"
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix in TEXT_TYPES:
        return TEXT_TYPES[suffix]
    stripped = head.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "application/json"
    if stripped.lower().startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if stripped.startswith(b"<?xml"):
        return "application/xml"
    return "text/plain"


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")  # never fails; keeps every byte as a character


class _Visible(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    p = _Visible()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _table_meta(rows: list[list[str]]) -> dict:
    header = rows[0] if rows else []
    return {"columns": header, "rows": max(0, len(rows) - 1), "sample": rows[1:1 + SAMPLE_ROWS]}


def _keys(value) -> list[str] | str:
    if isinstance(value, dict):
        return list(value)[:50]
    return type(value).__name__


def extract(data: bytes, media_type: str) -> tuple[str | None, dict]:
    """``(text, meta)`` for a text-tier file. A file that fails to parse keeps its text with ``meta.parse_error``."""
    text = decode(data)
    meta: dict = {}
    try:
        if media_type == "text/html":
            text = html_text(text)
        elif media_type in ("text/csv", "text/tab-separated-values"):
            delim = "\t" if media_type == "text/tab-separated-values" else ","
            meta = _table_meta(list(csv.reader(io.StringIO(text), delimiter=delim)))
        elif media_type == "application/json":
            value = json.loads(text)
            meta = {"top": _keys(value), "rows": len(value) if isinstance(value, list) else None,
                    "sample": value[:SAMPLE_ROWS] if isinstance(value, list) else None}
        elif media_type == "application/x-ndjson":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            meta = {"rows": len(rows), "top": _keys(rows[0]) if rows else [], "sample": rows[:SAMPLE_ROWS]}
        elif media_type == "application/yaml":
            value = yaml.safe_load(text)
            meta = {"top": _keys(value)}
        elif media_type == "application/xml":
            if b"<!ENTITY" in data:
                raise ValueError("XML entity declarations aren't accepted")
            rootel = ElementTree.fromstring(data)  # noqa: S314 - expat doesn't expand external entities
            meta = {"root": rootel.tag, "children": len(rootel)}
            text = " ".join(t.strip() for t in rootel.itertext() if t.strip())
    except (ValueError, yaml.YAMLError, ElementTree.ParseError, csv.Error) as exc:
        meta = {"parse_error": str(exc)[:200]}
    if len(text) > TEXT_CAP:
        meta["truncated"] = True
        text = text[:TEXT_CAP]
    return text, meta
