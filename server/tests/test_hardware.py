"""Hardware detection and the recommended harness profile (#296)."""
from __future__ import annotations

import json

from cli_agent import hardware
from cli_agent.agent import main


def _fake_root(tmp_path, ram_kb=None, os_name=None, devices=(), files=()):
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    (root / "etc").mkdir()
    (root / "dev").mkdir()
    if ram_kb is not None:
        (root / "proc" / "meminfo").write_text(f"MemTotal:       {ram_kb} kB\nMemFree:  1 kB\n")
    if os_name:
        (root / "etc" / "os-release").write_text(f'NAME="X"\nPRETTY_NAME="{os_name}"\n')
    for d in devices:
        (root / "dev" / d).write_text("")
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    return root


def test_small_board_gets_lite_sized_limits(tmp_path):
    root = _fake_root(tmp_path, ram_kb=512 * 1024, os_name="Raspberry Pi OS Lite")
    res = hardware.detect(str(tmp_path), str(root))
    assert res["version"] == 1
    assert res["facts"]["ram_bytes"] == 512 * 1024 * 1024
    assert res["facts"]["os"] == "Raspberry Pi OS Lite"
    assert res["facts"]["npu"] is False and res["facts"]["gpu_cuda"] is False
    assert res["profile"] == {"tier": "full", "heartbeat_seconds": 300, "max_records_per_minute": 60,
                              "can_host_hub": False}


def test_big_board_with_an_npu_can_host_a_hub(tmp_path, monkeypatch):
    root = _fake_root(tmp_path, ram_kb=16 * 1024 * 1024, os_name="Ubuntu 24.04.5 LTS",
                      devices=("fastrpc-cdsp",), files=("etc/systemd/system/arduino-router.service",))
    monkeypatch.setattr(hardware, "_free_disk", lambda path: 20 * hardware.GIB)
    res = hardware.detect(str(tmp_path), str(root))
    assert res["facts"]["npu"] is True and res["facts"]["mcu_bridge"] is True
    assert res["profile"]["can_host_hub"] is True
    assert res["profile"]["heartbeat_seconds"] == 60 and res["profile"]["max_records_per_minute"] == 600
    line = hardware.summary(res)
    assert "16384 MiB RAM" in line and "npu" in line and "mcu_bridge" in line and len(line) < 1000


def test_app_lab_daemon_alone_counts_as_an_mcu_bridge(tmp_path):
    root = _fake_root(tmp_path, ram_kb=2 * 1024 * 1024, files=("lib/systemd/system/arduino-app-cli.service",))
    assert hardware.detect(str(tmp_path), str(root))["facts"]["mcu_bridge"] is True


def test_tiny_or_unknown_memory_is_lite(tmp_path):
    assert hardware.profile({"ram_bytes": 128 * hardware.MIB})["tier"] == "lite"
    assert hardware.profile({})["tier"] == "lite"


def test_missing_files_give_nulls_not_errors(tmp_path):
    res = hardware.detect(str(tmp_path / "nowhere"), str(tmp_path / "no-root"))
    f = res["facts"]
    assert f["ram_bytes"] is None and f["os"] is None and f["disk_free_bytes"] is None
    assert f["npu"] is None and f["gpu_cuda"] is None and f["mcu_bridge"] is None
    assert res["profile"]["tier"] == "lite"


def test_cli_prints_the_profile(capsys, tmp_path):
    assert main(["hardware", "--data-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"version", "facts", "profile"}
