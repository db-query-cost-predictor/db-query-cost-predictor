"""Response provenance and corrupted-evidence refusal with synthetic payloads."""
from io import BytesIO
import json
import pytest
from rq2_pipeline.contract import default_config, validate_config
from rq2_pipeline.provider import generate
from rq2_pipeline.response import GenerationError, parse_response
from rq2_pipeline.replay import Evidence, replay


def response():
    return {"model": "openai/gpt-oss-120b", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"sql": "SELECT 1", "explanation": "Synthetic test"})}}], "usage": {"completion_tokens": 10}, "id": "synthetic"}


@pytest.mark.parametrize("change", ["model", "truncated", "extra-field"])
def test_incomplete_or_wrong_model_response_is_not_accepted(change):
    value = response()
    if change == "model": value["model"] = "other-model"
    elif change == "truncated": value["choices"][0]["finish_reason"] = "length"
    else: value["choices"][0]["message"]["content"] = '{"sql":"SELECT 1","explanation":"test","extra":"forbidden"}'
    with pytest.raises(GenerationError): parse_response(value, "openai/gpt-oss-120b")


def test_single_provider_call_records_provenance_without_the_key(tmp_path, monkeypatch):
    key = "TEST_ONLY_PROVIDER_SECRET"
    monkeypatch.setenv("GROQ_API_KEY", key)
    calls = []
    class Reply(BytesIO):
        headers = {"x-request-id": "synthetic"}
    def opener(request, timeout):
        calls.append(request)
        return Reply(json.dumps(response()).encode())
    context = {"database": "synthetic", "settings": {"jit": "off", "work_mem": "4MB"},
               "schema": {"columns": [["lineitem", "l_shipdate", "date", "NO"]], "constraints": [], "indexes": [], "row_counts": {"lineitem": 0}},
               "plain_explain": {"Plan": {"Node Type": "Result"}}}
    result = generate({"sql": "SELECT 1"}, context, default_config()["generation"], tmp_path, opener=opener)
    assert len(calls) == 1 and result["served_model"] == "openai/gpt-oss-120b"
    assert result["finish_reason"] == "stop" and result["usage"]["completion_tokens"] == 10
    assert all(key not in p.read_text() for p in tmp_path.iterdir())


def test_bool_is_not_a_valid_substitute_for_the_integer_generation_cap():
    config = default_config(); config["generation"]["max_calls_per_original"] = True
    with pytest.raises(ValueError): validate_config(config)


def test_tampered_evidence_stops_before_report_creation(tmp_path):
    root = tmp_path / "evidence"; root.mkdir()
    (root / "identity.json").write_text("{}")
    (root / "evidence-manifest.json").write_text(json.dumps({"scope": "FINAL_EVALUATION_Q06_Q20", "files": {"identity.json": "incorrect"}}))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="hash mismatch"): replay(root, output)
    assert not output.exists()


def test_evidence_reader_refuses_path_escape(tmp_path):
    (tmp_path / "evidence-manifest.json").write_text('{"scope":"FINAL_EVALUATION_Q06_Q20","files":{}}')
    reader = Evidence(tmp_path)
    with pytest.raises(ValueError, match="Unsafe"): reader.raw("../outside.json")
