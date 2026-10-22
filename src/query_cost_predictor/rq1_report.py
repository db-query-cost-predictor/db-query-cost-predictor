"""RQ1 report components and the final ``report.md`` assembly.

Each task stage renders its own Markdown section from its metric rows and
bootstrap summary and keeps it in the run context
(``context.artifacts["report_sections"]``); nothing is written by a stage.
:func:`write_report` assembles ``report.md`` once, as the last payload of the
final stage. Every report carries the YAML limitations block verbatim, the
STOP/G-08 scope note and the run provenance. A report that lacks any declared
section is marked PARTIAL and says it is not the RQ1 study.

The regression section labels its parts: the primary ``fold_group`` headline
(with paired bootstrap intervals and learned-minus-baseline differences), the
separate rank-metric table, operational and secondary metrics, per-fold and
stratified results, the ``fold_family`` robustness check and the
``fold_seen_template`` seen-template diagnostic. No model is named a winner.

The 1 s classification section has the same structure: the headline average
precision of all six classifiers with paired intervals and learned-minus-
baseline differences, a separate table for the one predeclared operating point
(probability >= 0.5), secondary ROC-AUC and Brier, per-fold and stratified
results with their flags and positive counts, and the labelled robustness and
seen-template parts. It states that 10 s classification is NOT_YET_EVALUABLE.
"""

from __future__ import annotations

from typing import Any, Sequence

from query_cost_predictor.rq1_config import Rq1Config
from query_cost_predictor.rq1_metrics import FLAGS, REASON_OUTPUT_TYPE, SCOPE_PER_FOLD, SCOPE_POOLED

REPORT_FILE = "report.md"
REPORT_SECTIONS = ("regression", "classification_1s", "diagnostics", "handoff")
METRIC_LABELS = {
    "log_mae": "log-MAE", "median_qerror": "median q-error", "p90_qerror": "p90 q-error",
    "p95_qerror": "p95 q-error", "mae_ms": "MAE (ms)", "rmse_ms": "RMSE (ms)", "r2_log": "R² (log; secondary)",
    "r2_ms": "R² (ms; secondary)", "spearman": "Spearman", "kendall": "Kendall",
    "average_precision": "average precision (AP)", "roc_auc": "ROC-AUC (secondary)", "recall": "recall",
    "false_negative_rate": "false-negative rate", "precision": "precision", "false_positive_rate": "false-positive rate",
    "warning_rate_per_100": "warnings per 100 cases", "brier": "Brier score",
}
ROBUSTNESS_LABEL = ("Robustness check: `fold_family` (whole structure families held out; stricter mainly within the "
                    "structural component; not complete schema-level separation)")
SEEN_TEMPLATE_LABEL = ("Seen-template diagnostic: `fold_seen_template` (whole query instances held out; templates "
                       "are seen in training, so this is never unseen-structure performance)")


class Rq1ReportError(ValueError):
    """A report section is missing or a report would misrepresent the study."""


def _fmt(value: float | None, reason: str | None = None) -> str:
    if value is None:
        if reason == REASON_OUTPUT_TYPE:
            return "n/a (output type)"
        return f"null ({reason})" if reason else "null"
    return f"{value:.4f}"


def _cell(row: dict[str, Any] | None, with_ci: bool = False) -> str:
    if row is None:
        return "-"
    text = _fmt(row["value"], row["null_reason"])
    if with_ci and row["value"] is not None:
        text += f" [{_fmt(row['ci_lower'])}, {_fmt(row['ci_upper'])}]" if row["ci_lower"] is not None else " [no interval]"
    return text


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _table(header: Sequence[str], body: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return lines


class _Rows:
    def __init__(self, rows: Sequence[dict[str, Any]]):
        self.index = {(r["fold_scheme"], r["model_id"], r["scope"], r["fold"], r["stratum_column"], r["stratum_value"],
                       r["metric"]): r for r in rows}
        self.models = list(dict.fromkeys(r["model_id"] for r in rows))
        self.slices: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for r in rows:
            self.slices.setdefault((r["fold_scheme"], r["model_id"], r["scope"], r["fold"], r["stratum_column"],
                                    r["stratum_value"]), []).append(r)

    def get(self, scheme: str, model: str, metric: str, scope: str = SCOPE_POOLED, fold: int | None = None,
            column: str | None = None, value: str | None = None) -> dict[str, Any] | None:
        return self.index.get((scheme, model, scope, fold, column, value, metric))

    def _slice(self, scheme: str, model: str, scope: str = SCOPE_POOLED, fold: int | None = None,
               column: str | None = None, value: str | None = None) -> list[dict[str, Any]]:
        return self.slices.get((scheme, model, scope, fold, column, value), [])

    def counts(self, scheme: str, model: str, **where: Any) -> str:
        rows = self._slice(scheme, model, **where)
        if not rows:
            return "-"
        row = rows[0]
        kpg = "null" if row["keys_per_group"] is None else f"{row['keys_per_group']:.2f}"
        text = f"{row['n_keys']} keys, {row['n_groups']} groups, {kpg} keys/group, {row['n_query_instances']} instances"
        if row["n_positive"] is not None:
            text += (f"; {row['n_positive']} positives in {row['n_positive_groups']} groups from "
                     f"{row['n_contributing_positive_folds']} fold(s)")
        return text

    def flags(self, scheme: str, model: str, **where: Any) -> str:
        """Every flag on any metric row of the slice, in the declared order."""
        present = {flag for row in self._slice(scheme, model, **where) for flag in (row["flag"] or "").split(";") if flag}
        return "; ".join(flag for flag in FLAGS if flag in present) or "none"


def _differences_table(bootstrap_summary: dict[str, Any]) -> list[str]:
    body = []
    for entry in bootstrap_summary.get("differences", []):
        interval = (f"[{_fmt(entry['ci_lower'])}, {_fmt(entry['ci_upper'])}]" if entry["ci_lower"] is not None
                    else "no valid replicates")
        reasons = "; ".join(f"{k}: {v}" for k, v in entry["invalid_reasons"].items()) or "none"
        body.append([f"`{entry['model_id']}`", f"`{entry['baseline_model_id']}`", METRIC_LABELS[entry["metric"]],
                     _fmt(entry["point_estimate"], entry["point_null_reason"]), interval,
                     f"{entry['n_valid']} / {entry['n_invalid']}", reasons])
    return _table(["Learned", "Baseline", "Metric", "Difference", "95 % interval", "Valid / invalid replicates",
                   "Invalid reasons"], body)


def _pooled_block(rows: _Rows, scheme: str, models: Sequence[str], metrics: Sequence[str], with_ci: bool) -> list[str]:
    body = [[f"`{m}`", *(_cell(rows.get(scheme, m, metric), with_ci) for metric in metrics)] for m in models]
    return _table(["Model", *(METRIC_LABELS[m] for m in metrics)], body)


def regression_section(metric_rows: Sequence[dict[str, Any]], bootstrap_summary: dict[str, Any], config: Rq1Config) -> str:
    """The regression section of ``report.md`` (Markdown; held in the run context until final assembly)."""
    rows = _Rows(metric_rows)
    tables = config.reporting["tables"]
    headline_models = [str(m) for m in tables["headline_regression"]["models"]]
    headline_metrics = [str(m) for m in tables["headline_regression"]["metrics"]]
    rank_models = [str(m) for m in tables["rank_metrics_regression"]["models"]]
    rank_metrics = [str(m) for m in tables["rank_metrics_regression"]["metrics"]]
    roles = {str(m["id"]): str(m["role"]) for m in config.regression_models}
    unc = config.uncertainty
    out = [
        "## Runtime regression",
        "",
        "Target: ln(`median_ms`). Runtime predictions are exp(predicted ln runtime) with no smearing correction: "
        "they estimate a typical (median-like) runtime, so ms-scale metrics (MAE, RMSE) are secondary. R² is "
        "secondary and never called accuracy. `reg_raw_cost_rank` is a ranking score, not a runtime prediction; "
        f"its runtime metrics are null because of its output type (\"{REASON_OUTPUT_TYPE}\", shown as "
        "n/a (output type)). The cost baselines are log-linear calibrations "
        "of planner cost; comparisons concern these specified baselines only. No model is named a winner, and no "
        "cross-validation score is an independent final test.",
        "",
        "### Primary: `fold_group`, pooled out-of-fold (headline)",
        "",
        f"Whole semantic groups held out ({rows.counts('fold_group', headline_models[0])}). Intervals are 95 % "
        f"paired cluster bootstrap percentile intervals over semantic groups ({unc['replicates']} replicates, seed "
        f"{unc['seed']}), conditional on the fixed out-of-fold predictions and folds.",
        "",
        *_pooled_block(rows, "fold_group", headline_models, headline_metrics, with_ci=True),
        "",
        "Roles: " + ", ".join(f"`{m}` {roles.get(m, '?')}" for m in headline_models) + ".",
        "",
        "### Primary: paired differences, learned minus calibrated baseline (`fold_group`)",
        "",
        f"Direction: {bootstrap_summary.get('direction', unc['differences']['regression']['direction'])}. A "
        "comparative claim must name the metric, the baseline and the paired interval; no universal or "
        "multiple-comparison-adjusted significance is claimed.",
        "",
    ]
    out += _differences_table(bootstrap_summary)
    out += ["", "### Primary: rank agreement (separate table; includes the raw-cost ranking)", "",
            *_pooled_block(rows, "fold_group", rank_models, rank_metrics, with_ci=False), "",
            "### Primary: operational and secondary metrics (`fold_group`, pooled)", "",
            *_pooled_block(rows, "fold_group", rank_models, ["mae_ms", "rmse_ms", "r2_log", "r2_ms"], with_ci=False),
            "", "### Primary: per fold (`fold_group`)", ""]
    per_fold_metrics = [*headline_metrics, "spearman"]
    body = []
    for model in rank_models:
        for fold in config.fold_values:
            body.append([f"`{model}`", str(fold), rows.counts("fold_group", model, scope=SCOPE_PER_FOLD, fold=fold),
                         *(_cell(rows.get("fold_group", model, m, SCOPE_PER_FOLD, fold)) for m in per_fold_metrics)])
    out += _table(["Model", "Fold", "Rows", *(METRIC_LABELS[m] for m in per_fold_metrics)], body)
    out += ["", "### Primary: strata (`fold_group`, pooled out-of-fold)", "",
            "Strata are reporting groups only (runtime bins use the actual `median_ms`); thin strata stay visible.", ""]
    strata_metrics = ["log_mae", "median_qerror", "p95_qerror", "spearman"]
    for column, declared in config.reporting["strata"].items():
        values = declared["labels"] if isinstance(declared, dict) else declared
        body = []
        for value in values:
            for model in rank_models:
                body.append([f"`{model}`", str(value),
                             rows.counts("fold_group", model, column=str(column), value=str(value)),
                             *(_cell(rows.get("fold_group", model, m, column=str(column), value=str(value)))
                               for m in strata_metrics)])
        out += [f"#### By `{column}`", "", *_table(["Model", "Stratum", "Rows", *(METRIC_LABELS[m] for m in strata_metrics)],
                                                   body), ""]
    for scheme, label in (("fold_family", ROBUSTNESS_LABEL), ("fold_seen_template", SEEN_TEMPLATE_LABEL)):
        out += [f"### {label}", "", f"Pooled out-of-fold ({rows.counts(scheme, rank_models[0])}); no bootstrap "
                "intervals (the paired bootstrap applies to the primary headline only).", "",
                *_pooled_block(rows, scheme, rank_models, [*headline_metrics, *rank_metrics], with_ci=False), ""]
    out += ["Complete long-format results (every scheme, model, fold and stratum, with null reasons) are in "
            "`metrics_regression.csv` and `metrics_regression.json`; out-of-fold outputs are in `oof_regression.csv`.",
            ""]
    return "\n".join(out)


def classification_section(metric_rows: Sequence[dict[str, Any]], bootstrap_summary: dict[str, Any],
                           config: Rq1Config) -> str:
    """The 1 s classification section of ``report.md`` (held in the run context until final assembly)."""
    rows = _Rows(metric_rows)
    tables = config.reporting["tables"]
    headline_models = [str(m) for m in tables["headline_classification"]["models"]]
    headline_metrics = [str(m) for m in tables["headline_classification"]["metrics"]]
    operating_models = [str(m) for m in tables["operating_point_classification"]["models"]]
    operating_metrics = [str(m) for m in tables["operating_point_classification"]["metrics"]]
    roles = {str(m["id"]): str(m["role"]) for m in config.raw["classification_models"]}
    unc = config.uncertainty
    ten = config.raw["targets"]["classification_10s"]
    flag_text = "; ".join(f"`{name}`: {text}" for name, text in config.metrics_spec["flags"].items())
    out = [
        "## High-runtime classification at 1 s",
        "",
        "Target: `hr_1s_class` (positive 1, negative 0) at the frozen 1,000 ms label boundary. Average precision (AP) "
        "is `sklearn.metrics.average_precision_score`, the non-interpolated step sum; it is never trapezoidal "
        "PR-AUC. `clf_raw_cost_rank` is a ranking score: it has AP and ROC-AUC only, and its probability and "
        "threshold metrics are n/a (output type). The cost baselines are logistic calibrations of log planner cost; "
        "comparisons concern these specified baselines only. No model is named a winner; accuracy is never a "
        "headline.",
        "",
        f"10 s classification: {ten['status']} ({ten['reason']}); nothing is trained or reported for it.",
        "",
        f"Positive concentration (recorded at the release audit): {config.reporting['positive_concentration_statement']}",
        "",
        f"Flags are descriptive; flagged results stay visible. {flag_text}.",
        "",
        "### Primary: `fold_group`, pooled out-of-fold (headline)",
        "",
        f"Whole semantic groups held out ({rows.counts('fold_group', headline_models[0])}). Intervals are 95 % "
        f"paired cluster bootstrap percentile intervals over semantic groups ({unc['replicates']} replicates, seed "
        f"{unc['seed']}; the same group draws as the regression intervals), conditional on the fixed out-of-fold "
        "predictions and folds.",
        "",
    ]
    body = [[f"`{m}`", roles.get(m, "?"), *(_cell(rows.get("fold_group", m, k), True) for k in headline_metrics),
             rows.flags("fold_group", m)] for m in headline_models]
    out += _table(["Model", "Role", *(METRIC_LABELS[k] for k in headline_metrics), "Flags"], body)
    model_summaries = {(e["model_id"], e["metric"]): e for e in bootstrap_summary.get("models", [])}
    invalid = [f"`{m}` {e['n_invalid']} ({'; '.join(f'{k}: {v}' for k, v in e['invalid_reasons'].items())})"
               for (m, _), e in model_summaries.items() if e["n_invalid"]]
    out += ["", "Invalid bootstrap replicates: " + (", ".join(invalid) if invalid else "none") + ".", "",
            "### Primary: paired differences, learned minus calibrated baseline (`fold_group`)", "",
            f"Direction: {bootstrap_summary.get('direction', unc['differences']['classification']['direction'])}. A "
            "comparative claim must name the metric, the baseline and the paired interval; no universal or "
            "multiple-comparison-adjusted significance is claimed.", "",
            *_differences_table(bootstrap_summary), "",
            "### Primary: one predeclared operating point (probability >= 0.5; separate table)", "",
            f"{_sentence(config.raw['fixed']['operating_point'])}. The threshold was fixed before any model was "
            "fitted and is not tuned.", "",
            *_pooled_block(rows, "fold_group", operating_models, operating_metrics, with_ci=False), "",
            "### Primary: secondary ranking and probability metrics (`fold_group`, pooled)", "",
            *_pooled_block(rows, "fold_group", headline_models, ["roc_auc", "brier"], with_ci=False), "",
            "### Primary: per fold (`fold_group`)", ""]
    per_fold_metrics = ["average_precision", "roc_auc", "recall", "precision", "brier"]
    body = []
    for model in headline_models:
        for fold in config.fold_values:
            where = {"scope": SCOPE_PER_FOLD, "fold": fold}
            body.append([f"`{model}`", str(fold), rows.counts("fold_group", model, **where),
                         *(_cell(rows.get("fold_group", model, m, SCOPE_PER_FOLD, fold)) for m in per_fold_metrics),
                         rows.flags("fold_group", model, **where)])
    out += _table(["Model", "Fold", "Rows", *(METRIC_LABELS[m] for m in per_fold_metrics), "Flags"], body)
    out += ["", "### Primary: strata (`fold_group`, pooled out-of-fold)", "",
            "Strata are reporting groups only (runtime bins use the actual `median_ms`, so the 1 s label is "
            "determined by the bin); thin and single-class strata stay visible with their flags.", ""]
    strata_metrics = ["average_precision", "recall", "false_positive_rate", "brier"]
    for column, declared in config.reporting["strata"].items():
        values = declared["labels"] if isinstance(declared, dict) else declared
        body = []
        for value in values:
            for model in headline_models:
                where = {"column": str(column), "value": str(value)}
                body.append([f"`{model}`", str(value), rows.counts("fold_group", model, **where),
                             *(_cell(rows.get("fold_group", model, m, **where)) for m in strata_metrics),
                             rows.flags("fold_group", model, **where)])
        out += [f"#### By `{column}`", "", *_table(["Model", "Stratum", "Rows", *(METRIC_LABELS[m] for m in strata_metrics),
                                                    "Flags"], body), ""]
    pooled_metrics = ["average_precision", "roc_auc", "recall", "precision", "false_positive_rate", "brier"]
    for scheme, label in (("fold_family", ROBUSTNESS_LABEL), ("fold_seen_template", SEEN_TEMPLATE_LABEL)):
        body = [[f"`{m}`", *(_cell(rows.get(scheme, m, k)) for k in pooled_metrics), rows.flags(scheme, m)]
                for m in headline_models]
        out += [f"### {label}", "", f"Pooled out-of-fold ({rows.counts(scheme, headline_models[0])}); no bootstrap "
                "intervals (the paired bootstrap applies to the primary headline only).", "",
                *_table(["Model", *(METRIC_LABELS[k] for k in pooled_metrics), "Flags"], body), ""]
    out += ["Complete long-format results (every scheme, model, fold and stratum, with null reasons, counts and flags) "
            "are in `metrics_classification.csv` and `metrics_classification.json`; out-of-fold outputs are in "
            "`oof_classification.csv`; the ten fixed calibration bins of every probabilistic model, scheme and slice "
            "(empty bins included) are in `calibration_bins.csv`.", ""]
    return "\n".join(out)


def render_report(config: Rq1Config, sections: dict[str, str], provenance: dict[str, Any], run_id: str) -> str:
    """The full ``report.md`` text: limitations verbatim, scope, provenance and the sections in declared order."""
    unknown = sorted(set(sections) - set(REPORT_SECTIONS))
    if unknown:
        raise Rq1ReportError(f"unknown report sections {unknown}")
    missing = [name for name in REPORT_SECTIONS if name not in sections]
    lines = [f"# RQ1 modeling report ({config.spec_id})", ""]
    if missing:
        lines += [f"> **PARTIAL REPORT.** Sections not included: {', '.join(missing)}. This is not the RQ1 study; "
                  "only a COMPLETE run of every declared stage counts as the RQ1 v1 study.", ""]
    original = config.original_validation
    source = provenance.get("source_state") or {}
    lines += [
        "## Scope and limitations", "",
        config.limitations_text.rstrip("\n"), "",
        "## Release status and scope", "",
        f"- Original release validation: {original['status']} ({', '.join(original['stop_codes'])}), preserved as "
        "recorded; the release is not PASS and nothing here relabels data or changes a threshold.",
        f"- 10 s classification: {config.raw['targets']['classification_10s']['status']} "
        f"({config.raw['targets']['classification_10s']['reason']}).",
        "- Rows: development cases only (`split_role == development`); `heldout_rq2` cases are never fitted, "
        "scaled, calibrated, selected or scored.",
        "- Folds: the stored `splits.csv` assignments, never regenerated.", "",
        "## Provenance", "",
        f"- Run id: `{run_id}`",
        f"- Source revision: `{source.get('source_revision', 'not recorded')}`",
        f"- Config SHA-256: `{provenance.get('config_sha256', config.sha256)}`",
        f"- Input record SHA-256: `{config.input_record_sha256}`",
        "",
    ]
    for name in REPORT_SECTIONS:
        if name in sections:
            lines += [sections[name].rstrip("\n"), ""]
    text = "\n".join(lines)
    if config.limitations_text not in text:
        raise Rq1ReportError("the limitations block is not reproduced verbatim")
    return text


def write_report(context: Any, required_sections: Sequence[str] = REPORT_SECTIONS) -> None:
    """Write ``report.md`` once, as the last payload; every required section must be present."""
    sections = dict(context.artifacts.get("report_sections", {}))
    missing = [name for name in required_sections if name not in sections]
    if missing:
        raise Rq1ReportError(f"report sections missing: {missing}")
    text = render_report(context.config, sections, context.provenance, context.run_id)
    context.write_payload(REPORT_FILE, text.encode("utf-8"))
