"""Handwritten negative controls for fixture validation, never LLM outputs.

These SQLs must not enter generation prompts, final-study candidate counts or
LLM outcome denominators. They test whether each fixture hazard is observable.
"""

import re

from rq2_pipeline.fixture_registry import GROUPS
from rq2_pipeline.safety import validate_read_only_sql


def _replace(sql, pattern, replacement):
    result, count = re.subn(pattern, replacement, sql, count=1, flags=re.I)
    if count != 1:
        raise ValueError("Original does not match the handwritten mutation contract")
    return result


def handwritten_mutations(query):
    family = GROUPS.get(query.get("semantic_group_id"))
    if family is None:
        raise ValueError("Unsupported mutation family")
    sql = validate_read_only_sql(query["sql"]).sql
    mutations = []
    def add(name, hazard, mutated):
        if mutated == sql:
            raise ValueError("Mutation must change the original")
        mutations.append({"name": name, "hazard": hazard, "source": "HANDWRITTEN_NEGATIVE_CONTROL",
                          "sql": validate_read_only_sql(mutated).sql})
    if family != "anti_subquery":
        add("null_to_zero", "zero_matching_rows",
            _replace(sql, r"\b(?:sum|avg)\s*\([^()]+\)", lambda match: f"coalesce({match.group()},0)"))
        add("distinct_aggregate", "duplicates",
            _replace(sql, r"\b(sum|avg)\s*\(", lambda match: match.group() + "DISTINCT "))
    if family == "date_arithmetic":
        start, days = query["parameters"]["start_date"], query["parameters"]["days"]
        add("exclude_lower_boundary", "off_by_one_boundary",
            sql + f" AND l_shipdate + {days} > DATE '{start}'")
    elif family == "correlated_sum":
        other = (query["parameters"]["nation_key"] + 1) % 25
        add("wrong_nation", "predicate_boundary",
            _replace(sql, r"s\.s_nationkey\s*=\s*[0-9]+", f"s.s_nationkey = {other}"))
    elif family == "anti_subquery":
        add("drop_customers_without_orders", "zero_matching_rows",
            sql + " AND EXISTS (SELECT 1 FROM orders seen WHERE seen.o_custkey=c.c_custkey)")
        add("exclude_lower_boundary", "off_by_one_boundary",
            _replace(sql, r"o\.o_orderdate\s*>=", "o.o_orderdate >"))
        if not sql.endswith(")"):
            raise ValueError("Expected original NOT IN subquery terminator")
        add("discard_duplicate_matches", "duplicates",
            sql[:-1] + " GROUP BY o.o_custkey HAVING count(*)=1)")
    elif family == "join_removal":
        add("exclude_lower_boundary", "off_by_one_boundary",
            _replace(sql, r"o\.o_orderdate\s*>=", "o.o_orderdate >"))
        add("constant_shippriority", "nonzero_shippriority",
            _replace(sql, r"max\s*\(\s*o\.o_shippriority\s*\)", "0::integer"))
    elif family == "materialized_cte":
        add("exclude_date_boundary", "off_by_one_boundary",
            _replace(sql, r"l_shipdate\s*>=", "l_shipdate >"))
        add("exclude_quantity_boundary", "off_by_one_boundary",
            _replace(sql, r"l_quantity\s*>=", "l_quantity >"))
    return tuple(mutations)
