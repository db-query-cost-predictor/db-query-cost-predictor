"""Public selected-query contract for the five supported SQL templates."""
from copy import deepcopy
from datetime import date
from importlib.resources import files
import json
import re

from .fixture_registry import GROUPS
from .hashing import sha256_text
from .protocol import CONFIGS
from .safety import SqlSafetyError, validate_read_only_sql
from .sqltext import tokenize
from .storage import digest

FAMILIES = {value: key for key, value in GROUPS.items()}
NAMES = {"date_arithmetic": "Date arithmetic", "correlated_sum": "Correlated aggregate",
         "anti_subquery": "NOT IN subquery", "join_removal": "Join elimination (original INNER JOIN)",
         "materialized_cte": "Materialized CTE"}
RESERVED_QUERY_IDS = frozenset({"con", "prn", "aux", "nul"}
                               | {prefix + str(n) for prefix in ("com", "lpt") for n in range(1, 10)})


def default_config():
    return json.loads(files("rq2_pipeline").joinpath("pipeline.json").read_text(encoding="utf-8"))


def validate_config(value):
    if digest(value) != digest(default_config()):
        raise ValueError("Configuration differs from the release defaults; version protocol changes separately")
    return deepcopy(value)


def _integer(parameters, name, lower=0, upper=3650):
    value = parameters[name]
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Invalid parameter: " + name)
    return value


def original_template(family, parameters):
    """Return the supported original shape after validating its parameters."""
    required = {"nation_key"} if family == "correlated_sum" else {"days", "start_date"}
    if family == "materialized_cte": required.add("quantity")
    if family not in FAMILIES or not isinstance(parameters, dict) or set(parameters) != required:
        raise ValueError("Unsupported family or parameter set")
    if family == "correlated_sum":
        nation = _integer(parameters, "nation_key", 0, 24)
        return ("SELECT s.s_suppkey, (SELECT sum(l.l_quantity) FROM lineitem l WHERE l.l_suppkey = s.s_suppkey) AS supplied_qty "
                f"FROM supplier s WHERE s.s_nationkey = {nation}")
    start = date.fromisoformat(parameters["start_date"]).isoformat()
    days = _integer(parameters, "days")
    if family == "date_arithmetic":
        return ("SELECT count(*) AS n_lines, sum(l_extendedprice) AS revenue FROM lineitem "
                f"WHERE l_shipdate + {days} BETWEEN DATE '{start}' AND DATE '{start}' + 6")
    if family == "anti_subquery":
        return ("SELECT count(*) AS n_customers FROM customer c WHERE c.c_custkey NOT IN "
                f"(SELECT o.o_custkey FROM orders o WHERE o.o_orderdate >= DATE '{start}' AND o.o_orderdate < DATE '{start}' + {days})")
    if family == "join_removal":
        return ("SELECT count(*) AS n_orders, avg(o.o_totalprice) AS avg_price, max(o.o_shippriority) AS max_priority "
                "FROM orders o JOIN customer c ON c.c_custkey = o.o_custkey "
                f"WHERE o.o_orderdate >= DATE '{start}' AND o.o_orderdate < DATE '{start}' + {days}")
    quantity = _integer(parameters, "quantity", 1, 1000)
    return ("WITH recent AS MATERIALIZED (SELECT l_orderkey, l_quantity, l_extendedprice FROM lineitem "
            f"WHERE l_shipdate >= DATE '{start}' AND l_shipdate < DATE '{start}' + {days}) "
            f"SELECT count(*) AS n_lines, sum(l_extendedprice) AS revenue FROM recent WHERE l_quantity >= {quantity}")


def token_identity(sql):
    return [(t.kind, t.value.lower() if t.kind == "word" else t.value)
            for t in tokenize(sql) if t.kind not in {"ws", "comment"}]


def load_queries(value):
    digest(value)  # Reject non-JSON values and NaN before any output or live I/O.
    if not isinstance(value, dict) or set(value) != {"version", "queries"} or value["version"] != "rq2-input-v1":
        raise ValueError("Expected rq2-input-v1 with version and queries")
    items = value["queries"]
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError("Supply one to fifty selected queries")
    result, ids, originals = [], set(), set()
    for item in items:
        required = {"id", "family", "sql", "parameters"}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {"selection"}:
            raise ValueError("A query needs id, family, sql and parameters; selection is optional")
        qid = item["id"]
        if (not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", qid)
                or qid.casefold() in ids or qid.casefold() in RESERVED_QUERY_IDS):
            raise ValueError("Query IDs must be unique ignoring case and must not be Windows device names")
        if not isinstance(item["sql"], str) or not item["sql"].strip() or not isinstance(item["family"], str):
            raise ValueError("SQL and family must be nonempty strings")
        selection = item.get("selection", {"method": "manual"})
        if (not isinstance(selection, dict) or set(selection) - {"method", "model_id", "prediction_id"}
                or selection.get("method") not in {"manual", "rq1"}
                or any(not isinstance(v, str) or not v.strip() for v in selection.values())
                or selection["method"] == "rq1" and not {"model_id", "prediction_id"} <= set(selection)):
            raise ValueError("Invalid selection provenance; RQ1 selection needs model_id and prediction_id")
        status, reason = "SUPPORTED", "Original matches a registered template"
        try:
            sql = validate_read_only_sql(item["sql"]).sql
        except SqlSafetyError:
            sql, status, reason = item["sql"], "UNSAFE", "Original SQL failed the read-only validator"
        if status == "SUPPORTED":
            try:
                canonical = original_template(item["family"], item["parameters"])
                if token_identity(sql) != token_identity(canonical):
                    raise ValueError("Original differs from the registered template")
            except (ValueError, KeyError, TypeError, OverflowError):
                status, reason = "REVIEW_REQUIRED", "Family, parameters or original SQL shape requires review"
        ids.add(qid.casefold())
        if status == "SUPPORTED":
            original_key = tuple(token_identity(sql))
            if original_key in originals:
                raise ValueError("Duplicate supported original SQL; select it once")
            originals.add(original_key)
        result.append({"id": qid, "sql": sql, "family": item["family"], "parameters": deepcopy(item["parameters"]),
                       "query_type": NAMES.get(item["family"], "Unsupported family"),
                       "semantic_group_id": FAMILIES.get(item["family"], "unsupported"),
                       "original_sha256": sha256_text(sql), "ordering_required": False, "preconditions": [],
                       "input_status": status, "input_reason": reason, "selection": deepcopy(selection),
                       "cases": [{"database": db, "configuration_name": cfg,
                                  "modeling_key": None} for db in ("tpch_sf0_1", "tpch_sf1") for cfg in CONFIGS]})
    return result
