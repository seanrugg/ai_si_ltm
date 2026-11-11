"""Document extraction (issue #244): pdf, docx, pptx, xlsx, epub in a child process."""
from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from server.services import extract
from server.services.extract import DOC_TYPES, extract_document, sniff
from server.tests.test_datasets_read import data_dir  # noqa: F401

PDF, DOCX, PPTX, XLSX, EPUB = (DOC_TYPES[s] for s in (".pdf", ".docx", ".pptx", ".xlsx", ".epub"))


def make_pdf(text: str, title: str = "Drive manual") -> bytes:
    """A one-page PDF with real text, built by hand (no PDF writer needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Title ({title}) >>".encode(),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def make_docx() -> bytes:
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.core_properties.title = "Docking"
    d.add_paragraph("Approach the dock at half speed.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "speed", "0.2 m/s"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    pptx = pytest.importorskip("pptx")
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Wheel stalls"
    slide.placeholders[1].text = "Back off, then retry."
    slide.notes_slide.notes_text_frame.text = "Seen on the left wheel."
    buf = io.BytesIO()
    deck.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Torque"
    ws.append(["joint", "max_nm"])
    ws.append(["shoulder", 12])
    ws.append(["elbow", "=B2/2"])  # a formula is read as its cached value, never evaluated
    wb.create_sheet("Empty")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   "<dc:title>Field guide</dc:title></metadata><manifest>"
                   '<item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest>'
                   '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr("OEBPS/a.xhtml", "<html><body><h1>Chapter one</h1><p>Charge overnight.</p></body></html>")
        z.writestr("OEBPS/b.xhtml", "<html><body><p>Chapter two: dock slowly.</p></body></html>")
    return buf.getvalue()


def _extract(tmp_path, data: bytes, media_type: str, **kw):
    path = tmp_path / "f"
    path.write_bytes(data)
    return asyncio.run(extract_document(str(path), media_type, **kw))


def test_sniffing_documents():
    assert sniff(make_pdf("x")[:64], "anything.bin") == PDF
    assert sniff(b"PK\x03\x04rest", "deck.pptx") == PPTX
    assert sniff(b"PK\x03\x04rest", "book.EPUB") == EPUB
    assert sniff(b"PK\x03\x04rest", "data.zip") == "application/zip"


def test_pdf(tmp_path):
    pytest.importorskip("pypdf")
    text, meta = _extract(tmp_path, make_pdf("Charge before the battery hits 20 percent"), PDF)
    assert "battery hits 20 percent" in text
    assert meta == {"pages": 1, "title": "Drive manual"}


def test_encrypted_pdf(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(make_pdf("secret")))
    writer.encrypt("pw")
    buf = io.BytesIO()
    writer.write(buf)
    assert _extract(tmp_path, buf.getvalue(), PDF) == (None, {"extract_error": "encrypted"})


def test_docx(tmp_path):
    text, meta = _extract(tmp_path, make_docx(), DOCX)
    assert "half speed" in text and "speed | 0.2 m/s" in text
    assert meta["title"] == "Docking" and meta["tables"] == 1


def test_pptx(tmp_path):
    text, meta = _extract(tmp_path, make_pptx(), PPTX)
    assert text.startswith("Slide 1") and "Back off" in text and "Notes: Seen on the left wheel." in text
    assert meta["slides"] == 1


def test_xlsx(tmp_path):
    text, meta = _extract(tmp_path, make_xlsx(), XLSX)
    assert text is not None, meta
    assert "# Torque" in text and "shoulder\t12" in text
    assert "=B2/2" not in text  # formulas are not read as text, nor evaluated
    assert meta["sheets"] == [{"name": "Torque", "rows": 3}, {"name": "Empty", "rows": 0}]


def test_epub_follows_the_spine(tmp_path):
    text, meta = _extract(tmp_path, make_epub(), EPUB)
    assert text.index("Charge overnight") < text.index("dock slowly")
    assert meta == {"chapters": 2, "title": "Field guide"}


def test_a_broken_document_is_an_error_not_a_crash(tmp_path):
    pytest.importorskip("docx")
    text, meta = _extract(tmp_path, b"PK\x03\x04 not really a docx", DOCX)
    assert text is None and meta["extract_error"]


def test_timeout(tmp_path):
    pytest.importorskip("pypdf")
    assert _extract(tmp_path, make_pdf("slow"), PDF, timeout=0) == (None, {"extract_error": "timeout"})


def test_missing_extra(tmp_path, monkeypatch):
    real = extract.importlib.util.find_spec
    monkeypatch.setattr(extract.importlib.util, "find_spec",
                        lambda name, *a: None if name == "pypdf" else real(name, *a))
    assert _extract(tmp_path, make_pdf("x"), PDF) == (None, {"extract_error": "install ltms[docs]"})


def test_text_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "TEXT_CAP", 10)
    text, meta = _extract(tmp_path, make_epub(), EPUB)
    assert len(text) == 10 and meta["truncated"] is True


def test_upload_stores_documents_and_failures(admin, monkeypatch):
    pytest.importorskip("docx")
    did = admin.post("/api/admin/datasets", json={"name": "Docs", "kind": "upload", "licence": "Internal"}).json()["id"]
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/items", files=[
        ("files", ("docking.docx", make_docx())),
        ("files", ("broken.docx", b"PK\x03\x04 nope")),
        ("files", ("torque.xlsx", make_xlsx())),  # stored without an extension on disk
    ])
    assert r.status_code == 200, r.text
    by_key = {i["key"]: i for i in r.json()["items"]}
    assert by_key["docking.docx"]["media_type"] == DOCX and "extract_error" not in by_key["docking.docx"]["meta"]
    assert by_key["broken.docx"]["meta"]["extract_error"]
    assert by_key["torque.xlsx"]["meta"]["sheets"][0] == {"name": "Torque", "rows": 3}
    admin.post(f"/api/admin/datasets/{did}/versions/1/approve")
    items = admin.get(f"/api/admin/datasets/{did}/versions/1/items").json()["items"]
    assert {i["key"] for i in items} == {"docking.docx", "broken.docx", "torque.xlsx"}
