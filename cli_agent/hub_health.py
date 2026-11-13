"""Health of the machine a hub runs on (#349): temperatures, memory, CPU and GPU
load, disk, power mode, over-current throttle counters, and named services.

Read-only and standard library only. Everything is read from files under
``root`` (``/`` in production, a fixture directory in tests) plus two optional
commands (``nvpmodel -q``, ``systemctl is-active``). Nothing here needs root.

The hub samples every ``HUB_HEALTH_SAMPLE_SECONDS`` and, every
``HUB_HEALTH_SECONDS``, signs one self-report (a ``perception`` tagged
``telemetry,internal,health``) with min/max/last over the window. A rise in an
over-current counter, or a temperature at or above ``HOT_C``, also signs an
``event`` tagged ``internal,health,throttle`` straight away, at most once per
``ALERT_GAP_S``.

The newest sample is also written to ``HUB_HEALTH_STATE`` (world-readable
JSON) so other local programs can read it without signing anything:

    python -m cli_agent.hub_health latest            # the JSON
    python -m cli_agent.hub_health latest --speak    # one sentence, for a voice assistant
    python -m cli_agent.hub_health probe             # what this machine exposes
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import Callable

SENSOR = "jetson-health"
HOT_C = 85.0
ALERT_GAP_S = 300.0
GPU_LOAD_GLOBS = ("sys/devices/platform/gpu.0/load", "sys/devices/platform/*.gpu/load",
                  "sys/devices/platform/*ga10b*/load", "sys/devices/platform/bus@0/*.gpu/load")
OC_GLOB = "sys/class/hwmon/hwmon*/oc*_event_cnt"


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def is_jetson(root: str = "/") -> bool:
    return os.path.exists(os.path.join(root, "etc/nv_tegra_release"))


def parse_services(spec: str | None) -> dict[str, str]:
    """``"ollama=http://127.0.0.1:11434/api/tags,wire-pod=systemd:wire-pod"`` → {name: check}."""
    out = {}
    for part in (spec or "").split(","):
        name, _, check = part.strip().partition("=")
        if name.strip() and check.strip():
            out[name.strip()] = check.strip()
    return out


class Reader:
    """One reading of this machine. ``run`` and ``fetch`` are replaceable for tests."""

    def __init__(self, root: str = "/", services: dict[str, str] | None = None,
                 run: Callable | None = None, fetch: Callable | None = None):
        self.root = root
        self.services = services or {}
        self.run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=5))
        self.fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=2).status)  # noqa: S310
        self._cpu_prev: tuple[int, int] | None = None

    def p(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def temps(self) -> dict[str, float]:
        out = {}
        for zone in sorted(glob.glob(self.p("sys/class/thermal/thermal_zone*"))):
            kind, raw = _read(os.path.join(zone, "type")), _read(os.path.join(zone, "temp"))
            try:
                c = int(raw) / 1000.0
            except (TypeError, ValueError):
                continue
            if c <= -40 or c > 150:  # disabled zones report nonsense
                continue
            name = (kind or os.path.basename(zone)).replace("-thermal", "").replace("_thermal", "").lower()
            out[name] = round(c, 1)
        return out

    def memory(self) -> dict[str, int]:
        info = {}
        for line in (_read(self.p("proc/meminfo")) or "").splitlines():
            key, _, rest = line.partition(":")
            try:
                info[key] = int(rest.split()[0])
            except (IndexError, ValueError):
                continue
        if "MemTotal" not in info:
            return {}
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return {"ram_total_mib": info["MemTotal"] // 1024, "ram_used_mib": (info["MemTotal"] - avail) // 1024}

    def cpu_pct(self) -> float | None:
        """Busy share of all CPU time since the previous call (None on the first)."""
        first = (_read(self.p("proc/stat")) or "").splitlines()[:1]
        try:
            vals = [int(x) for x in first[0].split()[1:]]
        except (IndexError, ValueError):
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals[:8])
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if prev is None or total <= prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def gpu_load_pct(self) -> float | None:
        for pattern in GPU_LOAD_GLOBS:
            for path in sorted(glob.glob(self.p(pattern))):
                raw = _read(path)
                try:
                    return round(int(raw) / 10.0, 1)  # per mille
                except (TypeError, ValueError):
                    continue
        return None

    def disk_used_pct(self) -> float | None:
        try:
            du = shutil.disk_usage(self.root)
        except OSError:
            return None
        return round(100.0 * du.used / du.total, 1) if du.total else None

    def oc_events(self) -> dict[str, int]:
        out = {}
        for path in sorted(glob.glob(self.p(OC_GLOB))):
            raw = _read(path)
            try:
                name = os.path.basename(path).split("_")[0]
                out[name] = out.get(name, 0) + int(raw)
            except (TypeError, ValueError):
                continue
        return out

    def power_mode(self) -> str | None:
        try:
            res = self.run(["nvpmodel", "-q"])
        except (OSError, subprocess.SubprocessError):
            return None
        for line in (getattr(res, "stdout", "") or "").splitlines():
            if "Power Mode" in line and ":" in line:
                return line.split(":", 1)[1].strip()
        return None

    def service(self, check: str) -> str:
        if check.startswith("systemd:"):
            try:
                res = self.run(["systemctl", "is-active", check.split(":", 1)[1]])
            except (OSError, subprocess.SubprocessError):
                return "unknown"
            return (getattr(res, "stdout", "") or "").strip() or "unknown"
        if check.startswith(("http://", "https://")):
            try:
                return "online" if 200 <= int(self.fetch(check)) < 400 else "offline"
            except Exception:  # noqa: BLE001 - any failure means offline
                return "offline"
        return "unknown"

    def sample(self) -> dict:
        s = {"temp_c": self.temps(), **self.memory(), "cpu_pct": self.cpu_pct(), "gpu_load_pct": self.gpu_load_pct(),
             "disk_used_pct": self.disk_used_pct(), "oc_events": self.oc_events(), "power_mode": self.power_mode()}
        if self.services:
            s["services"] = {name: self.service(check) for name, check in self.services.items()}
        return {k: v for k, v in s.items() if v not in (None, {}, "")}


NUMERIC = ("ram_used_mib", "cpu_pct", "gpu_load_pct", "disk_used_pct")


class Window:
    """min/max/last of numeric readings since the last report."""

    def __init__(self):
        self.samples: list[dict] = []

    def add(self, s: dict) -> None:
        self.samples.append(s)

    def report(self) -> dict | None:
        if not self.samples:
            return None
        last, out = self.samples[-1], {"sensor": SENSOR, "samples": len(self.samples)}
        for key in NUMERIC:
            vals = [s[key] for s in self.samples if isinstance(s.get(key), (int, float))]
            if vals:
                out[key] = {"min": min(vals), "max": max(vals), "last": vals[-1]}
        zones = sorted({z for s in self.samples for z in s.get("temp_c", {})})
        if zones:
            out["temp_c"] = {}
            for z in zones:
                vals = [s["temp_c"][z] for s in self.samples if z in s.get("temp_c", {})]
                out["temp_c"][z] = {"min": min(vals), "max": max(vals), "last": vals[-1]}
        for key in ("ram_total_mib", "power_mode", "oc_events", "services"):
            if key in last:
                out[key] = last[key]
        self.samples = []
        return out


class Monitor:
    """Samples, keeps the window, and says when to report or alert."""

    def __init__(self, reader: Reader, report_s: float, sample_s: float, now: float,
                 state_path: str | None = None, hot_c: float = HOT_C):
        self.reader, self.report_s, self.sample_s, self.hot_c = reader, report_s, sample_s, hot_c
        self.state_path = state_path
        self.window = Window()
        self.next_sample, self.next_report = now, now + report_s
        self.last_oc: dict[str, int] | None = None
        self.last_alert = -1e18
        self.throttled_in_window = False

    def poll(self, now: float) -> tuple[dict | None, dict | None]:
        """``(report, alert)``: either may be None. Call every hub tick."""
        alert = None
        if now >= self.next_sample:
            self.next_sample = now + self.sample_s
            s = self.reader.sample()
            self.window.add(s)
            self._save(s)
            alert = self._alert(s, now)
        report = None
        if now >= self.next_report:
            self.next_report = now + self.report_s
            report = self.window.report()
            if report is not None:
                report["throttled"] = self.throttled_in_window
                self.throttled_in_window = False
        return report, alert

    def _alert(self, s: dict, now: float) -> dict | None:
        oc = s.get("oc_events") or {}
        rises = {}
        if self.last_oc is not None:
            for k, v in oc.items():
                before = self.last_oc.get(k)
                if before is not None and v > before:  # a drop is a reboot reset, not an event
                    rises[k] = v - before
        self.last_oc = oc or self.last_oc
        hot = {z: c for z, c in (s.get("temp_c") or {}).items() if c >= self.hot_c}
        if not rises and not hot:
            return None
        self.throttled_in_window = True
        if now - self.last_alert < ALERT_GAP_S:
            return None
        self.last_alert = now
        parts = [f"{k} +{n}" for k, n in sorted(rises.items())] + [f"{z} {c:.0f} C" for z, c in sorted(hot.items())]
        return {"summary": "throttle: " + ", ".join(parts),
                "payload": {"sensor": SENSOR, "oc_rise": rises, "oc_events": oc, "hot_c": hot,
                            "power_mode": s.get("power_mode")}}

    def _save(self, s: dict) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"at": time.time(), **s}, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.state_path)
        except OSError:
            pass


def speak(s: dict) -> str:
    """One plain sentence from a sample, for a voice assistant."""
    parts = ["Jetson status report."]
    if s.get("cpu_pct") is not None:
        parts.append(f"CPU at {round(s['cpu_pct'])} percent.")
    if s.get("gpu_load_pct") is not None:
        parts.append(f"GPU at {round(s['gpu_load_pct'])} percent.")
    if s.get("ram_used_mib") is not None and s.get("ram_total_mib"):
        parts.append(f"Memory: {s['ram_used_mib']} of {s['ram_total_mib']} megabytes used.")
    if s.get("disk_used_pct") is not None:
        parts.append(f"Disk at {round(s['disk_used_pct'])} percent.")
    temps = s.get("temp_c") or {}
    if temps:
        z, c = max(temps.items(), key=lambda kv: kv[1])
        parts.append(f"Hottest is {z} at {round(c)} degrees Celsius.")
    if s.get("power_mode"):
        parts.append(f"Power mode {s['power_mode']}.")
    oc = s.get("oc_events") or {}
    if oc:
        parts.append("Throttle counters: " + ", ".join(f"{k} {v}" for k, v in sorted(oc.items())) + ".")
    for name, state in sorted((s.get("services") or {}).items()):
        word = {"active": "running", "online": "online", "inactive": "stopped", "failed": "failed",
                "offline": "offline"}.get(state, state)
        parts.append(f"{name} is {word}.")
    return " ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hub machine health (#349)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    latest = sub.add_parser("latest", help="the newest sample the hub saved")
    latest.add_argument("--state", default=os.environ.get("HUB_HEALTH_STATE"),
                        help="the hub's HUB_HEALTH_STATE file (default: $HUB_HEALTH_STATE)")
    latest.add_argument("--speak", action="store_true", help="one sentence instead of JSON")
    latest.add_argument("--max-age", type=float, default=600, help="older than this (s) counts as missing")
    sub.add_parser("probe", help="read this machine once and print what was found")
    args = ap.parse_args(argv)
    if args.cmd == "probe":
        r = Reader(services=parse_services(os.environ.get("HUB_HEALTH_SERVICES")))
        r.cpu_pct()
        time.sleep(1)
        print(json.dumps({"jetson": is_jetson(), **r.sample()}, indent=1))
        return 0
    if not args.state:
        print("give --state or set HUB_HEALTH_STATE", file=sys.stderr)
        return 2
    try:
        with open(args.state, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        print("no health reading available", file=sys.stderr)
        return 1
    if time.time() - float(s.get("at", 0)) > args.max_age:
        print("the newest health reading is too old", file=sys.stderr)
        return 1
    print(speak(s) if args.speak else json.dumps(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
