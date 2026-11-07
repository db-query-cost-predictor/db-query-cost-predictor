"""Regression checks for SQL, provider and portable filesystem boundaries."""
from io import BytesIO
import json

import pytest

from rq2_pipeline.contract import load_queries, original_template
from rq2_pipeline.response import GenerationError, parse_response
from rq2_pipeline.safety import SqlSafetyError, validate_read_only_sql
from rq2_pipeline.contract import default_config
from rq2_pipeline.pipeline import run
from rq2_pipeline.provider import generate


@pytest.mark.parametrize("statement", [
    r'SELECT U&"pg_sl\0065ep"(0)',
    r'''SELECT U&"d\0062link_exec"('synthetic', 'synthetic')''',
    r'''SELECT u&"pg_sl!0065ep" UESCAPE '!' (0)''',
    r'SELECT * FROM U&"publ\0069c".lineitem',
])
def test_unicode_escaped_identifiers_are_rejected_before_execution(statement):
    with pytest.raises(SqlSafetyError):
        validate_read_only_sql(statement)


@pytest.mark.parametrize("statement", [
    "SELECT pg_catalog.pg_sleep(0)",
    'SELECT "pg_sleep"(0)',
    "WITH changed AS (DELETE FROM lineitem RETURNING *) SELECT * FROM changed",
    "SELECT 1; SELECT 2",
    "SELECT * INTO copied FROM lineitem",
    "SELECT set_config('search_path','public',false)",
])
def test_write_and_side_effect_statements_are_rejected(statement):
    with pytest.raises(SqlSafetyError):
        validate_read_only_sql(statement)


@pytest.mark.parametrize("statement", [
    r'''SELECT 'U&"pg_sl\0065ep"' AS text_value''',
    r'''SELECT 1 /* U&"pg_sl\0065ep"(0) */''',
    r'''SELECT $$U&"pg_sl\0065ep"(0)$$ AS text_value''',
])
def test_identifier_like_text_in_literals_and_comments_is_not_executed(statement):
    assert validate_read_only_sql(statement).status == "PASSED_READ_ONLY_SINGLE_STATEMENT"


@pytest.mark.parametrize("choices", [None, {}, {"one": {}}, [None], [1], [[]],
    [{"finish_reason": "stop", "message": None}],
    [{"finish_reason": "stop", "message": []}],
    [{"finish_reason": "stop", "message": {"content": {}}}],
])
def test_malformed_response_shapes_are_generation_failures(choices):
    with pytest.raises(GenerationError):
        parse_response({"model": "openai/gpt-oss-120b", "choices": choices}, "openai/gpt-oss-120b")


@pytest.mark.parametrize("value", [None, [], 1, "synthetic"])
def test_non_object_provider_responses_are_generation_failures(value):
    with pytest.raises(GenerationError):
        parse_response(value, "openai/gpt-oss-120b")


@pytest.mark.parametrize("content", [
    '{"sql":"SELECT 1","sql":"SELECT 2","explanation":"Synthetic"}',
    '{"sql":"SELECT 1","explanation":"One","explanation":"Two"}',
])
def test_duplicate_candidate_fields_are_not_silently_replaced(content):
    response = {"model": "openai/gpt-oss-120b", "choices": [
        {"finish_reason": "stop", "message": {"content": content}}]}
    with pytest.raises(GenerationError):
        parse_response(response, "openai/gpt-oss-120b")


def selected(qid, days=17):
    parameters = {"start_date": "1994-04-13", "days": days}
    return {"id": qid, "family": "date_arithmetic", "parameters": parameters,
            "sql": original_template("date_arithmetic", parameters)}


@pytest.mark.parametrize("qid", ["CON", "prn", "AUX", "Nul", "COM1", "com9", "LPT1", "lpt9"])
def test_windows_device_names_are_not_query_directories(qid):
    with pytest.raises(ValueError):
        load_queries({"version": "rq2-input-v1", "queries": [selected(qid)]})


def test_query_ids_must_be_unique_on_case_insensitive_filesystems():
    with pytest.raises(ValueError):
        load_queries({"version": "rq2-input-v1", "queries": [selected("Example"), selected("example", 18)]})


def test_portable_ids_with_device_name_prefixes_still_work():
    queries = load_queries({"version": "rq2-input-v1", "queries": [selected("CON-example")]})
    assert queries[0]["input_status"] == "SUPPORTED"


def test_malformed_api_payload_is_retained_without_retrying_or_running_sql(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "TEST_ONLY_SECRET")
    from .test_contract_pipeline import FakeBackend
    context = {"database": "synthetic", "settings": {"jit": "off", "work_mem": "4MB"},
               "schema": {"columns": [["lineitem", "l_shipdate", "date", "NO"]],
                          "constraints": [], "indexes": [], "row_counts": {"lineitem": 0}},
               "plain_explain": {"Plan": {"Node Type": "Result"}}}
    calls = []
    class Reply(BytesIO):
        headers = {}
    def opener(request, timeout):
        calls.append(request)
        return Reply(json.dumps({"model": "openai/gpt-oss-120b", "choices": [1]}).encode())
    class Backend(FakeBackend):
        def generate(self, query, config, folder):
            self.calls += 1
            return generate(query, context, config, folder, opener=opener)
    backend = Backend()
    result = run({"version": "rq2-input-v1", "queries": [selected("response-check")]},
                 default_config(), tmp_path / "run", backend)
    assert len(calls) == backend.calls == 1
    assert backend.evaluations == 0
    assert result["case_counts"] == {"GENERATION_FAILED": 4}
    assert result["funnel"][0]["passed"] == 0
    assert (tmp_path / "run/generation/response-check/response-raw.json").exists()
