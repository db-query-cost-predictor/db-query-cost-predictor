"""Synthetic environment records, independent of the executing laptop."""

from copy import deepcopy

import pytest

from rq2_pipeline.environment import (
    EnvironmentChangedError, assert_environment_unchanged, environment_sha256, require_ac_power,
)


def record():
    return {"captured_at": "synthetic", "postgresql": {"version": "16.test", "version_num": 160000, "catalog_sha256": "synthetic"},
            "docker_engine": {"NCPU": 4, "MemTotal": 8000, "ServerVersion": "test", "KernelVersion": "test"},
            "docker_desktop_version": "test", "container_image_id": "synthetic",
            "container_resource_limits": {"Memory": 4000, "MemorySwap": 8000, "NanoCpus": 2000000000,
                                          "CpuQuota": 0, "CpuPeriod": 0, "CpusetCpus": "", "ShmSize": 1000},
            "windows_power": {"active_power_scheme": "381b4222-f694-41f0-9685-ff5bb260df2e (synthetic)",
                              "effective_overlay_guid": "00000000-0000-0000-0000-000000000000",
                              "ac_line_status": 1, "battery_saver_flag": 0}}


@pytest.mark.parametrize("section,key,value", [
    ("docker_engine", "NCPU", 8), ("docker_engine", "MemTotal", 9000),
    ("container_resource_limits", "Memory", 5000), ("container_resource_limits", "NanoCpus", 1000000000),
    ("postgresql", "version_num", 160001), ("windows_power", "ac_line_status", 0),
    ("windows_power", "effective_overlay_guid", "ded574b5-45a0-4f42-8737-46345c09c238"),
])
def test_material_environment_changes_are_refused(section, key, value):
    original = record()
    current = deepcopy(original)
    current[section][key] = value
    with pytest.raises(EnvironmentChangedError):
        assert_environment_unchanged(original, current)


def test_capture_timestamp_is_not_a_material_environment_change():
    original = record()
    current = deepcopy(original)
    current["captured_at"] = "later"
    assert environment_sha256(original) == environment_sha256(current)
    assert_environment_unchanged(original, current)


def test_battery_power_is_not_ready_for_final_timing():
    current = record()
    require_ac_power(current)
    current["windows_power"]["ac_line_status"] = 0
    with pytest.raises(ValueError, match="AC power"):
        require_ac_power(current)
