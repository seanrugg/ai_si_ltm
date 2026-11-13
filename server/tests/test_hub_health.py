"""Hub machine health (issue #349). Fixture sysfs trees; no real hardware."""
from __future__ import annotations

import json
import os
import types

import pytest

from cli_agent import hub_health as hh
from cli_agent.hub_main import load_config, make_health_monitor, record_health


def _w(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


@pytest.fixture
def jetson(tmp_path):
    root = str(tmp_path)
    _w(root, "etc/nv_tegra_release", "# R36 (release), REVISION: 4.3\n")
    _w(root, "sys/class/thermal/thermal_zone0/type", "cpu-thermal\n")
    _w(root, "sys/class/thermal/thermal_zone0/temp", "48500\n")
    _w(root, "sys/class/thermal/thermal_zone1/type", "gpu-thermal\n")
    _w(root, "sys/class/thermal/thermal_zone1/temp", "47000\n")
    _w(root, "sys/class/thermal/thermal_zone2/type", "tj-thermal\n")
    _w(root, "sys/class/thermal/thermal_zone2/temp", "-256000\n")  # disabled zone: ignored
    _w(root, "proc/meminfo", "MemTotal:        7802120 kB\nMemFree: 100 kB\nMemAvailable:    3901060 kB\n")
    _w(root, "proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\n")
    _w(root, "sys/devices/platform/gpu.0/load", "250\n")
    _w(root, "sys/class/hwmon/hwmon3/oc1_event_cnt", "0\n")
    _w(root, "sys/class/hwmon/hwmon3/oc3_event_cnt", "7\n")
    return root


def _run(args):
    if args[0] == "nvpmodel":
        return types.SimpleNamespace(stdout="NV Power Mode: 25W\n2\n")
    return types.SimpleNamespace(stdout="inactive\n")


def reader(root, services=None):
    return hh.Reader(root, services=services or {}, run=_run, fetch=lambda url: 200)


def test_reads_a_jetson(jetson):
    r = reader(jetson, {"ollama": "http://127.0.0.1:11434/api/tags", "wire-pod": "systemd:wire-pod"})
    s = r.sample()
    assert s["temp_c"] == {"cpu": 48.5, "gpu": 47.0}
    assert s["ram_total_mib"] == 7619 and s["ram_used_mib"] == 3809
    assert "cpu_pct" not in s  # needs two samples
    assert s["gpu_load_pct"] == 25.0 and s["power_mode"] == "25W"
    assert s["oc_events"] == {"oc1": 0, "oc3": 7}
    assert s["services"] == {"ollama": "online", "wire-pod": "inactive"}
    _w(jetson, "proc/stat", "cpu  150 0 150 900 0 0 0 0 0 0\n")  # 100 busy of 200
    assert r.sample()["cpu_pct"] == 50.0
    assert hh.is_jetson(jetson)


def test_window_report_and_throttle_alert(jetson, tmp_path):
    state = str(tmp_path / "hub-health.json")
    m = hh.Monitor(reader(jetson), report_s=300, sample_s=30, now=0, state_path=state)
    assert m.poll(0) == (None, None)  # first sample: counters baseline
    _w(jetson, "sys/class/thermal/thermal_zone0/temp", "52000\n")
    _w(jetson, "sys/class/hwmon/hwmon3/oc3_event_cnt", "9\n")
    report, alert = m.poll(30)
    assert report is None and alert["summary"] == "throttle: oc3 +2" and alert["payload"]["oc_rise"] == {"oc3": 2}
    _w(jetson, "sys/class/hwmon/hwmon3/oc3_event_cnt", "10\n")
    assert m.poll(60)[1] is None  # within the alert gap
    report, _ = m.poll(300)
    assert report["sensor"] == "jetson-health" and report["samples"] == 4 and report["throttled"] is True
    assert report["temp_c"]["cpu"] == {"min": 48.5, "max": 52.0, "last": 52.0}
    assert report["oc_events"] == {"oc1": 0, "oc3": 10}
    with open(state) as f:
        saved = json.load(f)
    assert saved["oc_events"]["oc3"] == 10 and oct(os.stat(state).st_mode & 0o777) == "0o644"
    # A reboot resets counters: not an alert.
    _w(jetson, "sys/class/hwmon/hwmon3/oc3_event_cnt", "0\n")
    assert m.poll(330 + hh.ALERT_GAP_S)[1] is None


def test_speak_says_what_was_recorded():
    s = {"cpu_pct": 12.4, "gpu_load_pct": 3, "ram_used_mib": 3800, "ram_total_mib": 7619, "disk_used_pct": 61.2,
         "temp_c": {"cpu": 48.5, "gpu": 51.2}, "power_mode": "25W", "oc_events": {"oc3": 7},
         "services": {"wire-pod": "inactive", "ollama": "online"}}
    assert hh.speak(s) == ("Jetson status report. CPU at 12 percent. GPU at 3 percent. Memory: 3800 of 7619 "
                           "megabytes used. Disk at 61 percent. Hottest is gpu at 51 degrees Celsius. Power mode "
                           "25W. Throttle counters: oc3 7. ollama is online. wire-pod is stopped.")


def test_latest_cli(tmp_path, capsys):
    state = tmp_path / "h.json"
    assert hh.main(["latest", "--state", str(state)]) == 1  # missing
    state.write_text(json.dumps({"at": 0, "cpu_pct": 5}))
    assert hh.main(["latest", "--state", str(state)]) == 1  # too old
    import time
    state.write_text(json.dumps({"at": time.time(), "cpu_pct": 5}))
    assert hh.main(["latest", "--state", str(state), "--speak"]) == 0
    assert capsys.readouterr().out.strip() == "Jetson status report. CPU at 5 percent."


def test_config_and_signing(jetson, tmp_path):
    base = {"LTMS_SERVER": "https://ltms.example.org", "LTMS_KEY_PATH": "k", "HUB_OUTBOX_PATH": str(tmp_path / "o.db")}
    cfg = load_config(base)
    assert cfg.health == "auto" and cfg.health_seconds == 300 and cfg.health_state_path == str(tmp_path / "hub-health.json")
    assert make_health_monitor(cfg, 0, root=str(tmp_path / "not-a-jetson")) is None
    assert make_health_monitor(cfg, 0, root=jetson) is not None
    assert make_health_monitor(load_config({**base, "HUB_HEALTH": "0"}), 0, root=jetson) is None
    with pytest.raises(ValueError):
        load_config({**base, "HUB_HEALTH": "maybe"})

    signed = []
    link = types.SimpleNamespace(append=lambda t, **k: signed.append((t, k)))
    assert record_health(link, {"sensor": "jetson-health"}, {"summary": "throttle: oc3 +1", "payload": {}}) == 2
    assert signed[0][0] == "event" and signed[0][1]["tags"] == ["internal", "health", "throttle"]
    assert signed[1][0] == "perception" and signed[1][1]["tags"] == ["telemetry", "internal", "health"]
