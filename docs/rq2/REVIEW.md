# Release 1.0.1 review

This post-study engineering review covers runtime source, input and response boundaries, credentials, database access, interruption handling, reporting and packaging. It does not rerun or change the closed Q06-Q20 study.

## Corrections

- **SQL identifiers:** PostgreSQL Unicode-escaped identifiers are rejected before execution. The previous lexer could inspect the encoded spelling instead of the decoded function name. Strings and comments containing similar text remain ordinary input data.
- **Provider responses:** unexpected response shapes and duplicate candidate JSON fields raise a generation failure. They cannot silently replace candidate fields or become unrelated runtime exceptions. The runner retains the failure across all four cases and does not retry.
- **Query directories:** Windows device names and query IDs that differ only by case are refused before output creation or live I/O.
- **Installation constraints:** list `psycopg` and `psycopg-binary` separately so the documented `pip -c` installation works with pip versions that reject extras in constraints. The dependency versions and runtime source are unchanged.

The generation prompt, fixture registry, measurement schedule and decision functions remain unchanged. Documentation clarifies zero-based pair order and the predeclared diagnostic versus its post-hoc withholding interpretation.

## Validation and scope

The release validation record lists the tests and runtime checks actually performed. Unit tests block model, database and host calls. Real engineering checks, if performed, stay separate from the historical study and never enter its denominators.

Validation passed 200 unit tests, an isolated wheel installation and replay of all 60 historical cases. Live checks covered all five families with original self-comparisons and negative controls. One fresh model output changed a date boundary and was rejected at snapshot comparison in all four environments, before timing. A separate handwritten date control completed all four verification and timing paths. The raw engineering records remain outside this delivery package; VALIDATION.json records their scope and counts.

Safety relies on the supported input contract, lexical checks, the prepared benchmark schema, a restricted PostgreSQL reader, rolled-back read-only transactions and execution limits. This release is not a general sandbox for arbitrary databases or installed user-defined functions. Correctness means agreement on tested snapshots and fixtures, not a proof for every database state.
