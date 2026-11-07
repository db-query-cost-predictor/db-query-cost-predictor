"""Stable environment identity and explicit drift refusal for local measurement."""

from hashlib import sha256
import json
import re


class EnvironmentChangedError(RuntimeError):
    pass


def environment_identity(record: dict) -> dict:
    postgres = record["postgresql"]
    engine = record["docker_engine"]
    limits = record["container_resource_limits"]
    power = record["windows_power"]
    required_limits = ("Memory", "MemorySwap", "NanoCpus", "CpuQuota", "CpuPeriod", "CpusetCpus", "ShmSize")
    if not all(key in limits for key in required_limits):
        raise ValueError("Container resource limits were not completely captured")
    if not all(type(engine.get(key)) is int and engine[key] > 0 for key in ("NCPU", "MemTotal")):
        raise ValueError("Docker engine CPU and memory must be known")
    if not power.get("effective_overlay_guid") or power.get("ac_line_status") not in {0, 1}:
        raise ValueError("Power mode and power source must be known")
    scheme = re.search(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", power["active_power_scheme"])
    if scheme is None:
        raise ValueError("Active power scheme GUID was not captured")
    return {
        "postgresql": {key: postgres[key] for key in ("version", "version_num", "catalog_sha256")},
        "docker_engine": {key: engine[key] for key in ("NCPU", "MemTotal", "ServerVersion", "KernelVersion")},
        "docker_desktop_version": record["docker_desktop_version"],
        "container_image_id": record["container_image_id"],
        "container_resource_limits": {key: limits[key] for key in required_limits},
        "windows_power": {"scheme_guid": scheme.group().lower(),
                          "overlay_guid": power["effective_overlay_guid"],
                          "ac_line_status": power["ac_line_status"],
                          "battery_saver_flag": power["battery_saver_flag"]},
    }


def environment_sha256(record: dict) -> str:
    payload = json.dumps(environment_identity(record), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def assert_environment_unchanged(baseline: dict, current: dict) -> None:
    if environment_identity(baseline) != environment_identity(current):
        raise EnvironmentChangedError("Environment identity changed; stop the study and preserve its evidence")


def require_ac_power(record: dict) -> None:
    if environment_identity(record)["windows_power"]["ac_line_status"] != 1:
        raise ValueError("Connect AC power before freezing or starting performance measurements")
