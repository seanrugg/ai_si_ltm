"""Metadata for robotics, sensor and media files (#247). No text: just what a
file holds (topics, shapes, dimensions, durations).

Runs as a child process of ``server.services.extract.extract_media``, in the
same sandbox as documents (30 s, 512 MB). Prints ``{"meta": {...}}`` or
``{"error": "..."}``. A missing optional package is reported as
``install ltms[media]`` (or the tool to install), never raised.

    python -m server.services.media_meta <path> <media-type>
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import zipfile

MAX_TOPICS = 200
MAX_ARRAYS = 100
MAX_TFRECORDS = 1_000_000

ROSBAG = "application/x-rosbag"
MCAP = "application/x-mcap"
DB3 = "application/x-ros2-db3"
PCD = "application/x-pcd"
PLY = "application/x-ply"
NPY = "application/x-npy"
NPZ = "application/x-npz"
HDF5 = "application/x-hdf5"
PARQUET = "application/x-parquet"
TFRECORD = "application/x-tfrecord"
IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/tiff"}
AUDIO = {"audio/wav", "audio/flac", "audio/mpeg", "audio/ogg"}
VIDEO = {"video/mp4", "video/x-matroska"}


class Missing(Exception):
    """An optional package or tool isn't installed."""


def _need(module: str, extra: str = "media"):
    try:
        return __import__(module, fromlist=["_"])
    except ImportError:
        raise Missing(f"install ltms[{extra}]") from None


def _ns_to_s(ns: int | None) -> float | None:
    return round(ns / 1e9, 3) if ns is not None else None


def _topics_meta(topics: dict[str, dict], start_ns, end_ns) -> dict:
    items = sorted(topics.items())
    meta = {"topics": [{"topic": t, **v} for t, v in items[:MAX_TOPICS]],
            "messages": sum(v["count"] for v in topics.values()),
            "start": _ns_to_s(start_ns), "end": _ns_to_s(end_ns),
            "duration_s": _ns_to_s(end_ns - start_ns) if start_ns is not None and end_ns is not None else None}
    if len(items) > MAX_TOPICS:
        meta["topics_total"] = len(items)
    return meta


def rosbag1(path: str) -> dict:
    _need("rosbags.rosbag1")
    from rosbags.rosbag1 import Reader

    with Reader(path) as r:
        topics: dict[str, dict] = {}
        for c in r.connections:
            t = topics.setdefault(c.topic, {"type": c.msgtype, "count": 0})
            t["count"] += c.msgcount
        return _topics_meta(topics, r.start_time, r.end_time if r.message_count else None)


def mcap_meta(path: str) -> dict:
    _need("mcap.reader")
    from mcap.reader import make_reader

    with open(path, "rb") as f:
        summary = make_reader(f).get_summary()
    if summary is None:
        return {"extract_error": "no summary section; re-record with a summary"}
    counts = summary.statistics.channel_message_counts if summary.statistics else {}
    topics: dict[str, dict] = {}
    for cid, ch in summary.channels.items():
        schema = summary.schemas.get(ch.schema_id)
        t = topics.setdefault(ch.topic, {"type": schema.name if schema else ch.message_encoding, "count": 0})
        t["count"] += counts.get(cid, 0)
    st = summary.statistics
    return _topics_meta(topics, st.message_start_time if st else None, st.message_end_time if st else None)


def db3(path: str) -> dict:
    """A rosbag2 SQLite file: read with the standard library, read-only."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT t.name, t.type, COUNT(m.id), MIN(m.timestamp), MAX(m.timestamp) "
            "FROM topics t LEFT JOIN messages m ON m.topic_id = t.id GROUP BY t.id").fetchall()
    finally:
        con.close()
    topics = {name: {"type": typ, "count": n} for name, typ, n, _, _ in rows}
    starts = [r[3] for r in rows if r[3] is not None]
    ends = [r[4] for r in rows if r[4] is not None]
    return _topics_meta(topics, min(starts) if starts else None, max(ends) if ends else None)


def _header_lines(path: str, stop: tuple[bytes, ...], limit: int = 200) -> list[str]:
    out = []
    with open(path, "rb") as f:
        for _ in range(limit):
            line = f.readline()
            if not line:
                break
            text = line.decode("ascii", "replace").strip()
            out.append(text)
            if line.startswith(stop):
                break
    return out


def pcd(path: str) -> dict:
    meta: dict = {}
    for line in _header_lines(path, (b"DATA",)):
        key, _, rest = line.partition(" ")
        if key == "FIELDS":
            meta["fields"] = rest.split()
        elif key == "POINTS":
            meta["points"] = int(rest)
        elif key == "DATA":
            meta["encoding"] = rest
    return meta


def ply(path: str) -> dict:
    meta: dict = {"elements": []}
    for line in _header_lines(path, (b"end_header",)):
        parts = line.split()
        if parts[:1] == ["format"]:
            meta["format"] = parts[1]
        elif parts[:1] == ["element"] and len(parts) == 3:
            meta["elements"].append({"name": parts[1], "count": int(parts[2]), "properties": []})
        elif parts[:1] == ["property"] and meta["elements"]:
            meta["elements"][-1]["properties"].append(parts[-1])
    vertex = next((e for e in meta["elements"] if e["name"] == "vertex"), None)
    if vertex:
        meta["points"], meta["fields"] = vertex["count"], vertex["properties"]
    return meta


def _npy_header(f) -> dict:
    np = _need("numpy")
    fmt = np.lib.format
    major, _ = fmt.read_magic(f)
    read = fmt.read_array_header_1_0 if major == 1 else fmt.read_array_header_2_0
    shape, _, dtype = read(f)  # the header only, never the data
    return {"shape": list(shape), "dtype": str(dtype)}


def npy(path: str) -> dict:
    with open(path, "rb") as f:
        return _npy_header(f)


def npz(path: str) -> dict:
    arrays = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist()[:MAX_ARRAYS]:
            if name.endswith(".npy"):
                with z.open(name) as f:
                    arrays.append({"name": name[:-4], **_npy_header(f)})
    return {"arrays": arrays}


def hdf5(path: str) -> dict:
    h5py = _need("h5py")
    out = []
    with h5py.File(path, "r") as f:
        def walk(group, prefix, depth):
            for name, obj in group.items():
                if len(out) >= MAX_ARRAYS:
                    return
                full = f"{prefix}/{name}"
                if isinstance(obj, h5py.Dataset):
                    out.append({"name": full, "shape": list(obj.shape), "dtype": str(obj.dtype)})
                elif depth < 2:
                    walk(obj, full, depth + 1)
        walk(f, "", 1)
    return {"datasets": out}


def parquet(path: str) -> dict:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise Missing("install pyarrow") from None
    md = pq.ParquetFile(path).metadata  # the footer only
    schema = md.schema.to_arrow_schema()
    return {"columns": [{"name": f.name, "type": str(f.type)} for f in schema], "rows": md.num_rows}


def tfrecord(path: str) -> dict:
    """Count records from the framing (length, crc, data, crc); features need TensorFlow."""
    count = 0
    with open(path, "rb") as f:
        while count < MAX_TFRECORDS:
            head = f.read(8)
            if len(head) < 8:
                break
            (length,) = struct.unpack("<Q", head)
            f.seek(4 + length + 4, 1)
            count += 1
    meta: dict = {"records": count}
    if count >= MAX_TFRECORDS:
        meta["records_capped"] = True
    return meta


def image(path: str) -> dict:
    _need("PIL")
    from PIL import Image

    with Image.open(path) as im:  # lazy: reads the header, not the pixels
        return {"width": im.width, "height": im.height, "mode": im.mode, "format": im.format,
                "frames": getattr(im, "n_frames", 1)}


def audio(path: str) -> dict:
    mutagen = _need("mutagen")
    f = mutagen.File(path)
    if f is None or f.info is None:
        return {"extract_error": "unrecognised audio"}
    info = f.info
    return {"duration_s": round(getattr(info, "length", 0.0), 3),
            "sample_rate": getattr(info, "sample_rate", None), "channels": getattr(info, "channels", None)}


def video(path: str) -> dict:
    exe = shutil.which("ffprobe")
    if exe is None:
        raise Missing("install ffmpeg (ffprobe) for video metadata")
    out = subprocess.run(  # noqa: S603 - fixed arguments, the path is ours
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, timeout=20, check=False)
    data = json.loads(out.stdout or b"{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = data.get("format", {}).get("duration")
    meta = {"duration_s": round(float(dur), 3) if dur else None, "width": v.get("width"),
            "height": v.get("height"), "codec": v.get("codec_name"), "audio": bool(a)}
    if not v:
        meta["extract_error"] = "no video stream found"
    return meta


EXTRACTORS = {ROSBAG: rosbag1, MCAP: mcap_meta, DB3: db3, PCD: pcd, PLY: ply, NPY: npy, NPZ: npz,
              HDF5: hdf5, PARQUET: parquet, TFRECORD: tfrecord}
EXTRACTORS.update({t: image for t in IMAGES})
EXTRACTORS.update({t: audio for t in AUDIO})
EXTRACTORS.update({t: video for t in VIDEO})
MEDIA_TYPES = set(EXTRACTORS)


def run(path: str, media_type: str) -> dict:
    try:
        return {"meta": EXTRACTORS[media_type](path)}
    except Missing as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2]), default=str))
