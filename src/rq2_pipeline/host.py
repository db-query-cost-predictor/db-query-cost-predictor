"""Read-only Windows/Docker/PostgreSQL probes for this local measurement host."""

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid

from rq2_pipeline.fixture_registry import capture_catalog


def command(args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("Read-only environment probe failed")
    return result.stdout.strip()


def power_record():
    if os.name != "nt":
        raise RuntimeError("This host probe currently supports Windows only")
    class Guid(ctypes.Structure):
        _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                    ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]
    class PowerStatus(ctypes.Structure):
        _fields_ = [("ac", wintypes.BYTE), ("battery", wintypes.BYTE), ("percent", wintypes.BYTE),
                    ("saver", wintypes.BYTE), ("life", wintypes.DWORD), ("full_life", wintypes.DWORD)]
    scheme = command([Path(os.environ["SystemRoot"]) / "System32/powercfg.exe", "/getactivescheme"])
    value = Guid()
    function = ctypes.WinDLL("powrprof.dll").PowerGetEffectiveOverlayScheme
    function.argtypes = [ctypes.POINTER(Guid)]
    function.restype = wintypes.DWORD
    if function(ctypes.byref(value)):
        raise RuntimeError("Power overlay identity unavailable")
    status = PowerStatus()
    if not ctypes.WinDLL("kernel32.dll").GetSystemPowerStatus(ctypes.byref(status)):
        raise RuntimeError("Power source identity unavailable")
    return {"active_power_scheme": scheme, "effective_overlay_guid": str(uuid.UUID(bytes_le=bytes(value))),
            "ac_line_status": status.ac, "battery_saver_flag": status.saver}


def capture_environment(conn, docker_path, container_id, desktop_path):
    info = json.loads(command([docker_path, "info", "--format", "{{json .}}"] ))
    config = json.loads(command([docker_path, "inspect", container_id, "--format", "{{json .HostConfig}}"] ))
    state = json.loads(command([docker_path, "inspect", container_id, "--format", "{{json .State}}"] ))
    if not state.get("Running") or state.get("Restarting") or state.get("Paused"):
        raise RuntimeError("Benchmark container is not stably running")
    running = command([docker_path, "ps", "--format", "{{.ID}}"] ).splitlines()
    canonical_id = command([docker_path, "inspect", container_id, "--format", "{{.Id}}"])
    if len(running) != 1 or not canonical_id.startswith(running[0]):
        raise RuntimeError("Stop competing Docker containers before measurement")
    image = command([docker_path, "inspect", container_id, "--format", "{{.Image}}"])
    quoted = str(desktop_path).replace("'", "''")
    version = command([Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe",
                       "-NoProfile", "-Command", f"(Get-Item -LiteralPath '{quoted}').VersionInfo.ProductVersion"])
    pg = conn.execute("SELECT current_setting('server_version'),current_setting('server_version_num')").fetchone()
    return {"captured_at": datetime.now(timezone.utc).isoformat(),
            "postgresql": {"version": pg[0], "version_num": int(pg[1]), "catalog_sha256": capture_catalog(conn).identity},
            "docker_engine": {key: info.get(key) for key in ("NCPU", "MemTotal", "ServerVersion", "OperatingSystem", "KernelVersion")},
            "docker_desktop_version": version, "container_image_id": image,
            "container_resource_limits": {key: config.get(key) for key in
                ("Memory", "MemorySwap", "NanoCpus", "CpuQuota", "CpuPeriod", "CpusetCpus", "ShmSize")},
            "windows_power": power_record()}
