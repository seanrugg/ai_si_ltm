"""Metadata for robotics, sensor and media files (issue #247)."""
from __future__ import annotations

import asyncio
import io
import shutil
import sqlite3
import struct
import subprocess
import wave

import pytest

from server.services import extract
from server.services.extract import extract_media, sniff


def _meta(tmp_path, data: bytes, name: str, **kw):
    path = tmp_path / "blob"  # the store keeps no extension
    path.write_bytes(data)
    mtype = sniff(data[:8192], name)
    return mtype, asyncio.run(extract_media(str(path), mtype, **kw))


def test_ros1_bag(tmp_path):
    pytest.importorskip("rosbags.rosbag1")
    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores, get_typestore

    ts = get_typestore(Stores.ROS1_NOETIC)
    String = ts.types["std_msgs/msg/String"]
    bag = tmp_path / "run.bag"
    with Writer(bag) as w:
        c = w.add_connection("/chatter", String.__msgtype__, typestore=ts)
        for i in range(3):
            w.write(c, (i + 1) * 1_000_000_000, ts.serialize_ros1(String(data=f"hi{i}"), String.__msgtype__))
    mtype, meta = _meta(tmp_path, bag.read_bytes(), "run.bag")
    assert mtype == "application/x-rosbag"
    assert meta["topics"] == [{"topic": "/chatter", "type": "std_msgs/msg/String", "count": 3}]
    assert meta["messages"] == 3 and meta["start"] == 1.0 and meta["duration_s"] == pytest.approx(2.0, abs=0.01)


def test_mcap(tmp_path):
    pytest.importorskip("mcap.writer")
    from mcap.writer import Writer

    buf = io.BytesIO()
    w = Writer(buf)
    w.start()
    sid = w.register_schema(name="sensor_msgs/Range", encoding="jsonschema", data=b"{}")
    cid = w.register_channel(topic="/tof", message_encoding="json", schema_id=sid)
    for i in range(4):
        w.add_message(channel_id=cid, log_time=(i + 10) * 1_000_000_000, publish_time=0, data=b"{}")
    w.finish()
    mtype, meta = _meta(tmp_path, buf.getvalue(), "run.mcap")
    assert mtype == "application/x-mcap"
    assert meta["topics"] == [{"topic": "/tof", "type": "sensor_msgs/Range", "count": 4}]
    assert meta["start"] == 10.0 and meta["end"] == 13.0


def test_ros2_db3(tmp_path):
    db = tmp_path / "run.db3"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE topics(id INTEGER PRIMARY KEY, name TEXT, type TEXT, serialization_format TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, topic_id INTEGER, timestamp INTEGER, data BLOB);
        INSERT INTO topics VALUES (1, '/cmd_vel', 'geometry_msgs/msg/Twist', 'cdr');
        INSERT INTO topics VALUES (2, '/odom', 'nav_msgs/msg/Odometry', 'cdr');
        INSERT INTO messages VALUES (1, 1, 5000000000, x''), (2, 1, 6000000000, x''), (3, 2, 7000000000, x'');
    """)
    con.commit()
    con.close()
    mtype, meta = _meta(tmp_path, db.read_bytes(), "run.db3")
    assert mtype == "application/x-ros2-db3"
    assert {t["topic"]: t["count"] for t in meta["topics"]} == {"/cmd_vel": 2, "/odom": 1}
    assert meta["duration_s"] == 2.0


def test_pcd_and_ply(tmp_path):
    pcd = b"# .PCD v0.7\nVERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\n" \
          b"WIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA ascii\n0 0 0 1\n1 1 1 2\n"
    mtype, meta = _meta(tmp_path, pcd, "scan.pcd")
    assert mtype == "application/x-pcd" and meta == {"fields": ["x", "y", "z", "intensity"], "points": 2,
                                                      "encoding": "ascii"}
    ply = b"ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\nproperty float y\n" \
          b"property float z\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n" + b"\x00" * 40
    mtype, meta = _meta(tmp_path, ply, "mesh.ply")
    assert mtype == "application/x-ply" and meta["points"] == 3 and meta["fields"] == ["x", "y", "z"]


def test_urdf_and_sdf_are_text_with_robot_meta(tmp_path):
    urdf = b'<?xml version="1.0"?><robot name="z3"><link name="base"/><link name="wheel"/>' \
           b'<joint name="axle" type="continuous"/></robot>'
    assert sniff(urdf, "z3.urdf") == "application/xml"
    text, meta = extract.extract(urdf, "application/xml")
    assert meta["format"] == "urdf" and meta["robot"] == "z3" and meta["links"] == 2 and meta["joints"] == 1
    sdf = b'<sdf version="1.9"><model name="arm"><link name="a"/><joint name="j"/></model></sdf>'
    _, meta = extract.extract(sdf, "application/xml")
    assert meta["robot"] == "arm" and meta["format"] == "sdf"


def test_npy_and_npz(tmp_path):
    np = pytest.importorskip("numpy")
    buf = io.BytesIO()
    np.save(buf, np.zeros((3, 4), dtype=np.float32))
    mtype, meta = _meta(tmp_path, buf.getvalue(), "depth.npy")
    assert mtype == "application/x-npy" and meta == {"shape": [3, 4], "dtype": "float32"}
    buf = io.BytesIO()
    np.savez(buf, joints=np.zeros(6), image=np.zeros((2, 2, 3), dtype=np.uint8))
    mtype, meta = _meta(tmp_path, buf.getvalue(), "pose.npz")
    assert mtype == "application/x-npz"
    assert {a["name"]: a["shape"] for a in meta["arrays"]} == {"joints": [6], "image": [2, 2, 3]}


def test_hdf5(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "d.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("imu", data=[[0.0, 0.0, 9.8]] * 5)
        g = f.create_group("episode_0")
        g.create_dataset("actions", shape=(10, 7), dtype="f4")
        g.create_group("deep").create_dataset("hidden", shape=(1,), dtype="f4")
    mtype, meta = _meta(tmp_path, path.read_bytes(), "d.h5")
    assert mtype == "application/x-hdf5"
    assert meta["datasets"] == [{"name": "/episode_0/actions", "shape": [10, 7], "dtype": "float32"},
                                {"name": "/imu", "shape": [5, 3], "dtype": "float64"}]


def test_parquet(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"joint": ["a", "b"], "nm": [1.5, 2.5]}), path)
    mtype, meta = _meta(tmp_path, path.read_bytes(), "t.parquet")
    assert mtype == "application/x-parquet" and meta["rows"] == 2
    assert [c["name"] for c in meta["columns"]] == ["joint", "nm"]


def test_tfrecord(tmp_path):
    recs = b"".join(struct.pack("<Q", len(d)) + b"\0" * 4 + d + b"\0" * 4 for d in (b"abc", b"defgh"))
    mtype, meta = _meta(tmp_path, recs, "train.tfrecord")
    assert mtype == "application/x-tfrecord" and meta == {"records": 2}


def test_image(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48)).save(buf, "PNG")
    mtype, meta = _meta(tmp_path, buf.getvalue(), "cam.png")
    assert mtype == "image/png" and meta["width"] == 64 and meta["height"] == 48 and meta["mode"] == "RGB"


def test_audio(tmp_path):
    pytest.importorskip("mutagen")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0\0\0" * 8000)
    mtype, meta = _meta(tmp_path, buf.getvalue(), "mic.wav")
    assert mtype == "audio/wav" and meta == {"duration_s": 1.0, "sample_rate": 8000, "channels": 2}


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg")
def test_video(tmp_path):
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x24:d=1", "-pix_fmt",
                    "yuv420p", "-c:v", "libx264", "-preset", "veryfast", str(out)], check=True)
    mtype, meta = _meta(tmp_path, out.read_bytes(), "v.mp4")
    assert mtype == "video/mp4" and meta["width"] == 32 and meta["height"] == 24
    assert meta["duration_s"] == pytest.approx(1.0, abs=0.1)


def test_video_without_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    _, meta = _meta(tmp_path, b"\0\0\0\x18ftypisom" + b"\0" * 100, "v.mp4")
    assert "ffprobe" in meta["extract_error"]


def test_missing_extra_and_bad_files_never_fail(tmp_path):
    # A file that claims hdf5 but isn't: an extract_error, not an exception.
    _, meta = _meta(tmp_path, b"\x89HDF\r\n\x1a\nnot really", "x.h5")
    assert "extract_error" in meta


def test_missing_package_is_named(tmp_path, monkeypatch):
    from server.services import media_meta

    monkeypatch.setitem(__import__("sys").modules, "h5py", None)  # import h5py -> ImportError
    path = tmp_path / "x"
    path.write_bytes(b"\x89HDF\r\n\x1a\n")
    assert media_meta.run(str(path), media_meta.HDF5) == {"error": "install ltms[media]"}


def test_timeout(tmp_path):
    pytest.importorskip("PIL")
    _, meta = _meta(tmp_path, b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "x.png", timeout=0)
    assert meta == {"extract_error": "timeout"}


def test_upload_stores_media_with_metadata(admin, app, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    app.state.config.data_dir = str(tmp_path / "data")
    did = admin.post("/api/admin/datasets", json={"name": "Cams", "kind": "upload", "licence": "Internal"}).json()["id"]
    buf = io.BytesIO()
    Image.new("L", (10, 20)).save(buf, "PNG")
    r = admin.post(f"/api/admin/datasets/{did}/versions/1/items", files=[("files", ("frame.png", buf.getvalue()))])
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["tier"] == "large" and item["meta"]["width"] == 10 and item["meta"]["mode"] == "L"
