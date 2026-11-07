"""Public input and durable orchestration tests with synthetic adapters."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from rq2_pipeline.contract import default_config, load_queries, original_template
from rq2_pipeline.journal import inspect_journal
from rq2_pipeline.pipeline import run
from rq2_pipeline.response import GenerationError


def manifest(qid="team-selected-example"):
    parameters = {"start_date": "1994-04-13", "days": 17}
    return {"version": "rq2-input-v1", "queries": [{"id": qid, "family": "date_arithmetic", "parameters": parameters,
            "sql": original_template("date_arithmetic", parameters)}]}


class FakeBackend:
    def __init__(self, *, failure=None, status="MEETS_PRIMARY_CRITERION"):
        self.calls, self.evaluations, self.preparations = 0, 0, 0
        self.failure, self.status = failure, status

    def prepare(self, queries, config, output):
        self.preparations += 1
        self.output = output
        return {"synthetic": True}

    def bind(self, identity): self.identity = identity
    def check(self, identity): pass

    def generate(self, query, config, folder):
        self.calls += 1
        assert inspect_journal(self.output / "execution-journal.jsonl")["states"][query["id"] + "/generation"] == "STARTED"
        if self.failure: raise self.failure("TEST_ONLY_FAILURE")
        return {"status": "GENERATED", "sql": "SELECT 1", "raw_sql_sha256": "synthetic"}

    def evaluate(self, identity, query, case, candidate, folder):
        self.evaluations += 1
        return {"status": self.status, "reason": "Synthetic adapter outcome", "verification_passed": self.status == "MEETS_PRIMARY_CRITERION"}


def test_arbitrary_selected_ids_run_once_and_keep_four_case_denominators(tmp_path):
    backend = FakeBackend()
    result = run(manifest(), default_config(), tmp_path / "run", backend)
    assert backend.calls == 1 and backend.evaluations == 4
    assert result["planned_queries"] == 1 and result["planned_cases"] == 4
    assert result["queries"][0]["recommended_in_all_four"]
    assert result["funnel"][0] == {"stage": "generation", "unit": "queries", "passed": 1, "denominator": 1}


@pytest.mark.parametrize("kind,expected", [("unsupported", "REVIEW_REQUIRED"), ("unsafe", "UNSAFE"), ("mislabeled", "REVIEW_REQUIRED")])
def test_unregistered_or_unsafe_input_never_calls_the_model_or_database(tmp_path, kind, expected):
    value = manifest()
    if kind == "unsupported": value["queries"][0]["family"] = "unregistered"
    elif kind == "unsafe": value["queries"][0]["sql"] = "DELETE FROM lineitem"
    else: value["queries"][0]["parameters"]["days"] += 1
    backend = FakeBackend()
    result = run(value, default_config(), tmp_path / "run", backend)
    assert backend.preparations == backend.calls == backend.evaluations == 0
    assert result["case_counts"] == {expected: 4}


def test_two_ids_for_the_same_original_do_not_buy_two_model_calls():
    value = manifest()
    extra = deepcopy(value["queries"][0]); extra["id"] = "another-id"
    extra["sql"] = "  " + extra["sql"] + "  -- whitespace variation"
    value["queries"].append(extra)
    with pytest.raises(ValueError, match="Duplicate supported original"):
        load_queries(value)


def test_generation_failure_is_retained_in_all_four_cases_without_retry(tmp_path):
    backend = FakeBackend(failure=GenerationError)
    result = run(manifest(), default_config(), tmp_path / "run", backend)
    assert backend.calls == 1 and backend.evaluations == 0
    assert result["case_counts"] == {"GENERATION_FAILED": 4}


def test_resume_never_repeats_an_interrupted_generation(tmp_path):
    output = tmp_path / "run"
    first = FakeBackend(failure=RuntimeError)
    with pytest.raises(RuntimeError): run(manifest(), default_config(), output, first)
    second = FakeBackend()
    result = run(manifest(), default_config(), output, second, resume=True)
    assert second.calls == second.evaluations == 0
    assert result["case_counts"] == {"INCONCLUSIVE": 4}


def test_closed_run_resume_is_read_only_and_tampering_is_refused(tmp_path):
    output = tmp_path / "run"
    result = run(manifest(), default_config(), output, FakeBackend())
    class NeverCalled(FakeBackend):
        def bind(self, identity): raise AssertionError("A closed resume must not open a live backend")
    assert run(manifest(), default_config(), output, NeverCalled(), resume=True) == result
    (output / "results.csv").write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="Closed run evidence changed"):
        run(manifest(), default_config(), output, NeverCalled(), resume=True)


def test_resume_refuses_changed_selection_or_configuration(tmp_path):
    output = tmp_path / "run"
    run(manifest(), default_config(), output, FakeBackend())
    with pytest.raises(ValueError): run(manifest("changed-id"), default_config(), output, FakeBackend(), resume=True)
    altered = default_config(); altered["generation"]["temperature"] = 0.9
    with pytest.raises(ValueError): run(manifest(), altered, output, FakeBackend(), resume=True)


def test_rq1_selection_provenance_stays_separate_from_generation():
    value = manifest()
    value["queries"][0]["selection"] = {"method": "rq1", "model_id": "test-model", "prediction_id": "test-prediction"}
    assert load_queries(value)[0]["selection"]["method"] == "rq1"
    del value["queries"][0]["selection"]["prediction_id"]
    with pytest.raises(ValueError): load_queries(value)
