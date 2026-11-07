"""Offline tests for the pinned prompt identity and context projection."""

from copy import deepcopy
from hashlib import sha256
import json
import socket
import subprocess
from pathlib import Path

import pytest

from rq2_pipeline import prompts
from rq2_pipeline.prompts import PROMPT_VERSION


CONFIG = Path(__file__).resolve().parents[3] / "src/rq2_pipeline/pipeline.json"
PINNED = json.loads(CONFIG.read_text(encoding="utf-8"))["generation"]


def build_generation_request(sql, supplied):
    return prompts.build_generation_request(sql, supplied,
                                            expected_template_sha256=PINNED["template_sha256"])


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Prompt construction must be offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("GROQ_API_KEY", "TEST_ONLY_ENVIRONMENT_SENTINEL")


def context():
    return {"database": "synthetic", "settings": {"work_mem": "4MB", "jit": "off"},
            "schema": {"columns": [["lineitem", "l_shipdate", "date", "NO"]],
                       "constraints": [], "indexes": [], "row_counts": {"lineitem": 1}},
            "plain_explain": {"Plan": {"Node Type": "Result"}}}


def test_request_identity_is_reproducible_and_contains_no_environment_secret():
    request = build_generation_request("SELECT 1 AS value", context())
    reordered = dict(reversed(list(context().items())))
    assert request == build_generation_request("SELECT 1 AS value", reordered)
    assert request.prompt_version == PROMPT_VERSION
    payload = {"prompt_version": PROMPT_VERSION, "messages": request.messages}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert request.prompt_sha256 == sha256(encoded).hexdigest()
    assert "TEST_ONLY_ENVIRONMENT_SENTINEL" not in str(request.messages)


def test_context_and_sql_changes_have_distinct_request_identities():
    original = build_generation_request("SELECT 1", context())
    changed_context = deepcopy(context())
    changed_context["settings"]["work_mem"] = "256kB"
    assert original.prompt_sha256 != build_generation_request("SELECT 2", context()).prompt_sha256
    assert original.prompt_sha256 != build_generation_request("SELECT 1", changed_context).prompt_sha256
    assert original.original_sql_sha256 == build_generation_request("SELECT 1", changed_context).original_sql_sha256


def test_returned_message_mutation_cannot_change_the_saved_request():
    request = build_generation_request("SELECT 1", context())
    external = request.messages
    external[1]["content"] = "changed"
    assert request.messages[1]["content"] != "changed"


@pytest.mark.parametrize("field", ["reference_sql", "historical_runtime", "labels", "api_key"])
def test_additional_top_level_context_fields_are_refused(field):
    supplied = context()
    supplied[field] = "DO_NOT_SEND"
    with pytest.raises(ValueError, match="Unexpected"):
        build_generation_request("SELECT 1", supplied)


@pytest.mark.parametrize("sql", ["", "  ", None])
def test_missing_original_is_refused(sql):
    with pytest.raises(ValueError):
        build_generation_request(sql, context())


def test_incomplete_or_non_json_context_is_refused():
    supplied = context()
    del supplied["schema"]
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)
    supplied = context()
    supplied["schema"] = "not a catalog projection"
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)
    supplied = context()
    supplied["plain_explain"]["Plan"]["Total Cost"] = float("nan")
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)


def test_template_is_pinned_independently_of_the_prompt_version(monkeypatch):
    assert PINNED["prompt_version"] == PROMPT_VERSION
    assert PINNED["template_sha256"] == prompts.template_sha256()
    monkeypatch.setattr(prompts, "INSTRUCTIONS", prompts.INSTRUCTIONS + " changed")
    with pytest.raises(ValueError, match="pinned"):
        build_generation_request("SELECT 1", context())


@pytest.mark.parametrize("path", ["schema", "counts", "plan", "child_plan", "settings"])
def test_nested_reference_or_runtime_fields_cannot_enter_generation(path):
    supplied = context()
    if path == "schema":
        supplied["schema"]["reference_sql"] = "DO_NOT_SEND"
    elif path == "counts":
        supplied["schema"]["row_counts"]["reference_sql"] = 999
    elif path == "plan":
        supplied["plain_explain"]["Execution Time"] = 10.0
    elif path == "child_plan":
        supplied["plain_explain"]["Plan"]["Plans"] = [{"Node Type": "Result", "reference_sql": "DO_NOT_SEND"}]
    else:
        supplied["settings"]["api_key"] = "DO_NOT_SEND"
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)


def test_preexecution_planning_duration_is_not_forwarded():
    supplied = context()
    supplied["plain_explain"]["Planning Time"] = 123.456
    request = build_generation_request("SELECT 1", supplied)
    assert "Planning Time" not in request.user_message
    assert "123.456" not in request.user_message


def test_wrong_nested_catalog_shapes_and_nullability_are_refused():
    supplied = context()
    supplied["schema"]["columns"][0] = {"reference_sql": "DO_NOT_SEND"}
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)
    supplied = context()
    supplied["schema"]["columns"][0][-1] = "unknown"
    with pytest.raises(ValueError):
        build_generation_request("SELECT 1", supplied)
