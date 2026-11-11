"""Document text extraction (#244): pdf, docx, pptx, xlsx, epub.

Run as a child process by ``server.services.extract.extract_document``, never
in the server process: a hostile or broken document can only exhaust its own
time (30 s) and memory (512 MB) limits. Prints one JSON object
``{"text", "meta"}`` or ``{"error"}`` on stdout. Nothing here runs macros:
the libraries only read the file's parts.

    python -m server.services.doc_extract <path> <media-type>
"""
from __future__ import annotations

import json
import posixpath
import sys
import zipfile
from xml.etree import ElementTree

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EPUB = "application/epub+zip"

MAX_XLSX_ROWS = 100_000  # rows read per workbook, all sheets together
MAX_EPUB_BYTES = 64 * 1024 * 1024  # uncompressed bytes read from an epub


class Encrypted(Exception):
    pass


def _pdf(path: str) -> tuple[str, dict]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise Encrypted()
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    meta: dict = {"pages": len(pages)}
    title = (reader.metadata or {}).get("/Title") if reader.metadata else None
    if title:
        meta["title"] = str(title)
    return "\n\n".join(p for p in pages if p), meta


def _docx(path: str) -> tuple[str, dict]:
    import docx

    doc = docx.Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    meta: dict = {"paragraphs": len(doc.paragraphs), "tables": len(doc.tables)}
    if doc.core_properties.title:
        meta["title"] = doc.core_properties.title
    return "\n".join(parts), meta


def _pptx(path: str) -> tuple[str, dict]:
    from pptx import Presentation

    deck = Presentation(path)
    slides = []
    for i, slide in enumerate(deck.slides, 1):
        lines = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            lines.append("Notes: " + slide.notes_slide.notes_text_frame.text.strip())
        if lines:
            slides.append(f"Slide {i}\n" + "\n".join(lines))
    meta: dict = {"slides": len(deck.slides)}
    if deck.core_properties.title:
        meta["title"] = deck.core_properties.title
    return "\n\n".join(slides), meta


def _xlsx(path: str) -> tuple[str, dict]:
    import openpyxl

    # read_only streams rows; data_only reads cached values, never formulas or macros.
    # A file object, not the path: the store's files have no extension, which
    # openpyxl would refuse.
    fh = open(path, "rb")  # noqa: SIM115 - closed below, after the lazy read
    wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
    out, sheets, budget = [], [], MAX_XLSX_ROWS
    for ws in wb.worksheets:
        rows = 0
        out.append(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if budget <= 0:
                break
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                out.append("\t".join(cells).rstrip("\t"))
                rows += 1
                budget -= 1
        sheets.append({"name": ws.title, "rows": rows})
    meta: dict = {"sheets": sheets}
    if budget <= 0:
        meta["rows_capped"] = MAX_XLSX_ROWS
    if wb.properties.title:
        meta["title"] = wb.properties.title
    wb.close()
    fh.close()
    return "\n".join(out), meta


def _epub(path: str) -> tuple[str, dict]:
    from server.services.extract import html_text

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        order: list[str] = []
        title = None
        try:
            container = ElementTree.fromstring(z.read("META-INF/container.xml"))  # noqa: S314
            opf_path = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
            opf = ElementTree.fromstring(z.read(opf_path))  # noqa: S314
            base = posixpath.dirname(opf_path)
            manifest = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("}item")}
            for el in opf.iter():
                if el.tag.endswith("}itemref") and el.get("idref") in manifest:
                    order.append(posixpath.normpath(posixpath.join(base, manifest[el.get("idref")])))
                if el.tag.endswith("}title") and el.text and title is None:
                    title = el.text.strip()
        except (KeyError, StopIteration, ElementTree.ParseError):
            order = []
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        parts, used = [], 0
        for name in order:
            if name not in names:
                continue
            info = z.getinfo(name)
            used += info.file_size
            if used > MAX_EPUB_BYTES:
                break
            parts.append(html_text(z.read(name).decode("utf-8", "replace")))
    meta: dict = {"chapters": len(parts)}
    if title:
        meta["title"] = title
    return "\n\n".join(p for p in parts if p), meta


EXTRACTORS = {PDF: _pdf, DOCX: _docx, PPTX: _pptx, XLSX: _xlsx, EPUB: _epub}


def run(path: str, media_type: str) -> dict:
    try:
        text, meta = EXTRACTORS[media_type](path)
    except Encrypted:
        return {"error": "encrypted"}
    except Exception as exc:  # noqa: BLE001 - reported to the parent, never raised
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"text": text, "meta": meta}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2])))
