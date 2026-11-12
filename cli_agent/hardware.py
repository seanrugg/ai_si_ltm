"""What the machine running the harness can do, and settings that suit it (#296).

Reads only local files (no network, no root) and never raises: a fact that
can't be read is ``None``. ``detect()`` returns::

    {"version": 1, "facts": {...}, "profile": {...}}

The profile is a recommendation an admin can override in LTMS.
"""
from __future__ import annotations

import glob
import os
import platform
import shutil

MIB = 1024 * 1024
GIB = 1024 * MIB


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _mem_total_bytes(root: str) -> int | None:
    text = _read(os.path.join(root, "proc/meminfo"))
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024  # kB
            except (IndexError, ValueError):
                return None
    return None


def _os_release(root: str) -> str | None:
    text = _read(os.path.join(root, "etc/os-release"))
    if not text:
        return None
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k] = v.strip().strip('"')
    return fields.get("PRETTY_NAME") or fields.get("NAME")


def _any(root: str, *patterns: str) -> bool | None:
    """True if any pattern matches under ``root``; None if ``root`` itself can't be read."""
    if not os.path.isdir(root):
        return None
    return any(glob.glob(os.path.join(root, p)) for p in patterns)


def _free_disk(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def detect(data_dir: str = ".", root: str = "/") -> dict:
    """Facts about this machine and a recommended profile. ``root`` lets tests use a fake filesystem."""
    try:
        cpus = os.cpu_count()
    except Exception:  # noqa: BLE001
        cpus = None
    facts = {
        "arch": platform.machine() or None,
        "cpus": cpus,
        "ram_bytes": _mem_total_bytes(root),
        "disk_free_bytes": _free_disk(data_dir),
        "os": _os_release(root),
        # Hexagon/other NPUs: FastRPC compute DSP devices, or the kernel's accel class.
        "npu": _any(root, "dev/fastrpc-cdsp*", "dev/accel*"),
        "gpu_cuda": _any(root, "dev/nvhost-*", "proc/driver/nvidia"),
        # Arduino App Lab boards (Linux + MCU): the router service that bridges Linux and the
        # MCU (arduino-router.service, confirmed on a VENTUNO Q), or the App Lab daemon.
        "mcu_bridge": _any(root, *(f"{d}/{unit}" for d in ("etc/systemd/system", "lib/systemd/system",
                                                            "usr/lib/systemd/system")
                                   for unit in ("arduino-router.service", "arduino-app-cli.service"))),
    }
    return {"version": 1, "facts": facts, "profile": profile(facts)}


def profile(facts: dict) -> dict:
    """Recommended settings from the facts. Unknown RAM is treated as small."""
    ram = facts.get("ram_bytes") or 0
    disk = facts.get("disk_free_bytes") or 0
    big = ram >= GIB
    return {
        "tier": "full" if ram >= 256 * MIB else "lite",
        "heartbeat_seconds": 60 if big else 300,
        "max_records_per_minute": 600 if big else 60,
        "can_host_hub": ram >= 2 * GIB and disk >= 2 * GIB,
    }


def summary(result: dict) -> str:
    """One compact line for an enrollment note (well under its 1000 characters)."""
    f, p = result["facts"], result["profile"]
    ram = f"{f['ram_bytes'] // MIB} MiB RAM" if f.get("ram_bytes") else "RAM unknown"
    flags = [k for k in ("npu", "gpu_cuda", "mcu_bridge") if f.get(k)]
    return (f"hardware: {f.get('arch') or '?'}, {f.get('cpus') or '?'} CPU, {ram}, {f.get('os') or 'OS unknown'}"
            f"{', ' + ', '.join(flags) if flags else ''}; profile {p['tier']}, "
            f"heartbeat {p['heartbeat_seconds']} s, hub-capable {'yes' if p['can_host_hub'] else 'no'}")[:900]
