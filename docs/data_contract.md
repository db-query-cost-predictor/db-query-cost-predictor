# Data contract — feature availability

The machine-readable source of truth is
[`config/feature_registry.yaml`](../config/feature_registry.yaml). Every field
used anywhere — every evidence-table column, every model feature and every
working column — has exactly one **availability class** (or, for the two
feature-store value columns, the list A–D) and one **role**.

## Availability classes

| Class | Meaning | Examples |
|---|---|---|
| **A** | SQL structure: SQL text and supplied parameters | `sql_join_keyword_count`, `sql_subquery_count`, parameter values |
| **B** | Schema and database statistics available before execution | `snap_scale_factor`, snapshot hashes, planner statistics |
| **C** | Environment and configuration | `cfg_work_mem_kb`, effective settings, timeout, protocol version |
| **D** | Plain pre-execution `EXPLAIN` estimates | `est_total_cost_log`, `est_plan_rows_log`, node-type counts, `est_workers_planned` |
| **E** | Execution-only outcome — **never a model input** | execution time, actual rows, actual loops, buffers, temporary blocks, I/O timing, WAL, launched workers, spill flags, runtime status, labels |

The plain PostgreSQL estimated total cost is class **D**: a legitimate feature
and the RQ1 baseline (raw rank baseline and fold-calibrated baseline).

## Roles

| Role | May enter a model feature matrix? |
|---|---|
| `feature` | **yes**, if its single class is A, B, C or D |
| `identifier`, `group_key`, `split_control` | never — used only for grouping, splitting, auditing and reporting |
| `provenance`, `annotation` | never |
| `label`, `diagnostic` | never (class E) |
| `feature_store` | storage columns of `feature_record`; the class is stored per row (A–D only) |

`template_id`, `semantic_group_id`, `query_instance_id`, `modeling_key`,
`family`, `configuration_name` and similar identities are **never predictors**,
even though they are known before execution. Designer annotations such as
`expected_mechanism` and coverage tags are never predictors either.

## Enforcement (written, not run by Claude Code)

| Where | Mechanism |
|---|---|
| `contract.assert_feature_matrix_allowed` | rejects unregistered columns (fail closed), class E and every non-`feature` role |
| `pilot_audit` | calls the contract before building the model matrix; data-quality check DQ-19 |
| `smoke_derive` | calls the contract before writing `features.csv` |
| `smoke_validate` | check V-16 re-runs the contract on the feature columns |
| `qcp.feature_record` | `CHECK (availability_class IN ('A','B','C','D'))` makes E-class storage impossible |
| `plans.estimate_view` | strips every execution-only key before any D-class feature is computed |
| `tests/unit/test_feature_contract.py` | E-class and identifier rejection; every produced feature registered; every migration column registered |

## Pilot caveat

The pilot captured only `EXPLAIN ANALYZE` plans. Its D-class features are
computed from the *estimate view* of those plans (all `Actual *`, buffer, I/O,
WAL, sort/hash runtime and worker-launch keys removed). This is what a plain
`EXPLAIN` returns under the same statistics and settings, but no plain plan was
captured; the audit states this in every output. New collections capture a
plain `EXPLAIN (SETTINGS, FORMAT JSON)` before any execution.

## Relation to the Research Plan's lettering

The Research and Implementation Plan used the same letters with different
meanings. This repository follows the prompt's definitions above.

| Research Plan | This repository |
|---|---|
| A request time (SQL, parameters, AST) | A |
| B plain plan time | D |
| C catalog time (statistics, indexes, configuration, hardware) | B (statistics, indexes) and C (configuration, hardware) |
| D execution time (actual rows, buffers, timing) | E |
| E future knowledge (rewrite outcomes, later statistics, fold encodings) | E (outcomes); fold-fitted encodings are prevented by fitting preprocessing inside folds |

## Changing the contract

1. Add the field to `config/feature_registry.yaml` with class, role and description.
2. If it is a new evidence column, add it to a new migration file (never edit an applied migration).
3. Update [data_dictionary.md](data_dictionary.md).
4. The registry tests fail until migrations and registry agree.
