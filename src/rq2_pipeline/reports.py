"""One final table and traceable per-query/per-environment output."""
from collections import Counter
import csv
from html import escape
import json
from pathlib import Path
from statistics import median

from .storage import encode, replace_text


def timing_medians(timing):
    pairs = timing.get("ab_pairs", [])
    if len(pairs) != 11 or any(p["a_us"] is None or p["b_us"] is None for p in pairs):
        return {"original_median_ms": None, "rewrite_median_ms": None}
    return {"original_median_ms": median(p["a_us"] for p in pairs)/1000,
            "rewrite_median_ms": median(p["b_us"] for p in pairs)/1000}


def write_report(output, rows, queries, *, scope, identity_sha256, generations=None):
    output = Path(output)
    expected = {(q["id"], c["database"], c["configuration_name"]) for q in queries for c in q["cases"]}
    observed = [(r["query_id"], r["database"], r["configuration"]) for r in rows]
    if set(observed) != expected or len(observed) != len(expected):
        raise ValueError("Rows do not reconcile to the selected query/environment queue")
    if generations is None:
        generations = []
        for query in queries:
            path = output / "generation" / query["id"] / "result.json"
            generations.append({"query_id": query["id"], **(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "NOT_STARTED"})})
    counts = Counter(r["status"] for r in rows)
    recommendations = [{"query_id": q["id"], "recommended_in_all_four": len(selected := [r for r in rows if r["query_id"] == q["id"]]) == 4
                        and all(r["status"] == "MEETS_PRIMARY_CRITERION" for r in selected)} for q in queries]
    summary = {"scope": scope, "identity_sha256": identity_sha256, "planned_queries": len(queries),
               "planned_cases": len(rows), "case_counts": dict(counts), "queries": recommendations, "cases": rows,
               "generation_provenance": generations,
               "funnel": [{"stage": stage, "unit": "queries", "passed": sum(g["status"] == "GENERATED" for g in generations), "denominator": len(queries)} for stage in ("generation", "response_valid")]
                    + [{"stage": stage, "unit": "environment cases", "passed": sum(r.get("gates", {}).get(stage) == "PASSED" for r in rows), "denominator": len(rows)}
                       for stage in ("safety", "preconditions", "snapshot", "fixtures", "aa", "timed")],
               "correctness_scope": "Agreement on tested snapshots and fixtures, not proof of equivalence",
               "time_columns": "Separate medians of instrumented server execution time; decisions use paired comparisons"}
    replace_text(output / "summary.json", json.dumps(summary, indent=2, default=encode) + "\n")
    fields = ["query_id", "query_type", "database", "configuration", "status", "verification_passed", "original_median_ms", "rewrite_median_ms", "reason"]
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fields, extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    replace_text(output / "results.csv", buffer.getvalue())
    display = lambda value: "" if value is None else str(value)
    table = "".join("<tr>" + "".join("<td>" + escape(display(r.get(k))) + "</td>" for k in fields) + "</tr>" for r in rows)
    funnel = "".join("<tr><td>" + escape(r["stage"]) + "</td><td>" + escape(r["unit"]) + "</td><td>" + str(r["passed"]) + "/" + str(r["denominator"]) + "</td></tr>" for r in summary["funnel"])
    headers = "".join("<th>" + escape(f) + "</th>" for f in fields)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>RQ2 pipeline results</title>'
            '<style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}td,th{padding:9px;border:1px solid #ccc}th{background:#00753d;color:white}</style>'
            '<h1>SQL rewrite evaluation</h1><p>Derived from TPC-H; not official or comparable TPC-H benchmark results.</p>'
            '<p>Scope: ' + escape(scope) + '. Finite snapshot and fixture checks do not prove general equivalence.</p>'
            '<p>Time columns show separate medians in milliseconds. Decisions use individual paired comparisons. Empty time cells mean timing did not complete or was not attempted.</p>'
            '<p><a href="summary.json">Structured evidence index</a> · <a href="results.csv">CSV</a></p>'
            '<h2>Funnel</h2><table><tr><th>Stage</th><th>Unit</th><th>Passed/planned</th></tr>' + funnel + '</table><h2>Cases</h2>'
            '<table><thead><tr>' + headers + '</tr></thead><tbody>' + table + '</tbody></table></html>')
    replace_text(output / "report.html", page)
    return summary
