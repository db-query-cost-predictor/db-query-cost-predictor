# Input, output and team integration

The public CLI and Python runner receive a selected-query manifest with version `rq2-input-v1` and a `queries` list. Every item has a unique safe `id`, `family`, `parameters` and original `sql`. See [the complete example file](../../examples/rq2/selected_queries.json). One to fifty originals may be supplied, with duplicates refused before generation.

IDs start with an ASCII letter, followed by at most 63 ASCII letters, digits, underscores or hyphens. They must be unique ignoring case; Windows device names such as CON, NUL, COM1 and LPT1 are refused before any live operation.

| Family | Parameters | Supported original |
| --- | --- | --- |
| date_arithmetic | start_date, days | lineitem count/SUM with l_shipdate + days BETWEEN start and start + 6 |
| correlated_sum | nation_key | supplier filter and correlated SUM(l_quantity) by supplier key |
| anti_subquery | start_date, days | customer count with NOT IN an orders date window |
| join_removal | start_date, days | orders/customer inner join with count, average price and maximum ship priority |
| materialized_cte | start_date, days, quantity | materialized lineitem date window followed by quantity filter and aggregates |

These are registered shapes, not automatic family classification. Parameter/SQL mismatches, new shapes and unsupported families become REVIEW_REQUIRED without an API call. Unsafe originals become UNSAFE. Canonical originals can be built with `rq2_pipeline.contract.original_template(family, parameters)`. The original shape, values and declared schema must be reviewed when adapting a new family.

Unicode-escaped SQL identifiers are unsupported and rejected, including in generated candidates. The lexical validator must not interpret an encoded function or relation name as a different identifier from PostgreSQL.

An optional `selection` object records `method` (manual or rq1). RQ1 selection additionally requires `model_id` and `prediction_id`. Selection metadata is stored for lineage and never sent to the language model. The provider receives only the original, validated schema/constraints/indexes/counts, settings and a plain EXPLAIN plan.

## Integration boundary

The RQ1/backend owner supplies selected originals and their provenance; this package does not select a prediction threshold or run the prediction model. The pipeline owns rewrite generation, safety, snapshot/fixture checks, paired measurement and decisions. The backend can consume summary.json and results.csv. Passing selection metadata is not a comparative gating-policy experiment.

For direct Python use, call `rq2_pipeline.pipeline.run(manifest, config, output, backend)` with `default_config()` and a `LiveBackend` context manager. The CLI is the supported simplest entry point. There is no HTTP service in this release.

## Outputs

`summary.json` contains every planned environment case, generation provenance, gate counts and all-four query decisions. `results.csv` and `report.html` show status, tested verification, separate original/rewrite medians in ms and a reason. Candidate SQL and exact responses are in generation/<id>; original SQL and source identity are in identity.json and selected-input.json. Case evidence is under cases/<id>/<database>/<configuration>.

Statuses include GENERATION_FAILED, UNSAFE, PRECONDITION_FAILED, NOT_EQUIVALENT, FIXTURE_REJECTED, REVIEW_REQUIRED, UNCHANGED, INCONCLUSIVE, AA_BLOCKED and the four timing labels. A wrong or unverified candidate cannot receive a successful timing recommendation. Untouched cases after an interrupted run remain NOT_STARTED until resumed.

Rejecting a rewrite preserves the original for the caller. Recommendations are provisional within tested snapshots and fixtures; no automatic query deployment occurs.
