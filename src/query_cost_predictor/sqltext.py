"""Small PostgreSQL-aware SQL lexer and text-derived (A-class) helpers.

This is *not* a SQL parser. It tokenizes PostgreSQL SQL well enough to

* ignore comments and the contents of string literals, dollar-quoted strings
  and quoted identifiers when scanning for keywords (used by ``safety.py``);
* produce a literal-insensitive structural fingerprint of a query;
* extract literal values in order (parameter diversity in the pilot audit);
* compute simple SQL-structure counts (availability class A).

Lexing errors (for example an unterminated string) raise ``SqlLexError``;
callers must treat that as a failure, never as an empty fingerprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from query_cost_predictor.hashing import normalize_sql_text, sha256_text


class SqlLexError(ValueError):
    """Raised when SQL text cannot be tokenized."""


@dataclass(frozen=True)
class Token:
    kind: str  # ws, comment, string, qident, number, word, param, op, punct, semicolon
    text: str  # exact source text
    value: str  # decoded value (strings), lowercased word, or the text itself


_NUMBER_RE = re.compile(r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_WORD_RE = re.compile(r"[A-Za-z_\u0080-￿][A-Za-z0-9_$\u0080-￿]*")
_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_PARAM_RE = re.compile(r"\$\d+")
_OP_CHARS = set("+-*/<>=~!@#%^&|`?")
_PUNCT_CHARS = set("(),[].:\\{}")


def _read_quoted(sql: str, start: int, quote: str, backslash_escapes: bool) -> tuple[int, str]:
    """Return (end_index_exclusive, decoded_value) for a quoted literal starting at ``start``."""
    i = start
    n = len(sql)
    buf: list[str] = []
    while True:
        if i >= n:
            raise SqlLexError(f"unterminated {quote} literal starting at offset {start}")
        ch = sql[i]
        if backslash_escapes and ch == "\\":
            if i + 1 >= n:
                raise SqlLexError("dangling backslash in E'' string")
            buf.append(sql[i:i + 2])
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and sql[i + 1] == quote:
                buf.append(quote)
                i += 2
                continue
            return i + 1, "".join(buf)
        buf.append(ch)
        i += 1


def tokenize(sql: str) -> list[Token]:
    """Tokenize ``sql`` into a list of tokens (including whitespace and comments)."""
    tokens: list[Token] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            j = i + 1
            while j < n and sql[j].isspace():
                j += 1
            tokens.append(Token("ws", sql[i:j], " "))
            i = j
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            tokens.append(Token("comment", sql[i:j], ""))
            i = j
            continue
        if sql.startswith("/*", i):
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth > 0:
                raise SqlLexError(f"unterminated block comment at offset {i}")
            tokens.append(Token("comment", sql[i:j], ""))
            i = j
            continue
        if ch in "eE" and i + 1 < n and sql[i + 1] == "'":
            end, value = _read_quoted(sql, i + 2, "'", backslash_escapes=True)
            tokens.append(Token("string", sql[i:end], value))
            i = end
            continue
        if ch == "'":
            end, value = _read_quoted(sql, i + 1, "'", backslash_escapes=False)
            tokens.append(Token("string", sql[i:end], value))
            i = end
            continue
        if ch == '"':
            end, value = _read_quoted(sql, i + 1, '"', backslash_escapes=False)
            tokens.append(Token("qident", sql[i:end], value))
            i = end
            continue
        if ch == "$":
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                marker = tag.group(0)
                close = sql.find(marker, tag.end())
                if close == -1:
                    raise SqlLexError(f"unterminated dollar-quoted string at offset {i}")
                tokens.append(Token("string", sql[i:close + len(marker)], sql[tag.end():close]))
                i = close + len(marker)
                continue
            param = _PARAM_RE.match(sql, i)
            if param:
                tokens.append(Token("param", param.group(0), param.group(0)))
                i = param.end()
                continue
            tokens.append(Token("op", "$", "$"))
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            match = _NUMBER_RE.match(sql, i)
            if match is None:  # pragma: no cover - regex always matches a digit
                raise SqlLexError(f"bad number at offset {i}")
            tokens.append(Token("number", match.group(0), match.group(0)))
            i = match.end()
            continue
        if ch.isalpha() or ch == "_" or ord(ch) > 127:
            match = _WORD_RE.match(sql, i)
            if match is None:  # pragma: no cover
                raise SqlLexError(f"bad identifier at offset {i}")
            tokens.append(Token("word", match.group(0), match.group(0).lower()))
            i = match.end()
            continue
        if ch == ";":
            tokens.append(Token("semicolon", ";", ";"))
            i += 1
            continue
        if sql.startswith("::", i):
            tokens.append(Token("punct", "::", "::"))
            i += 2
            continue
        if ch in _PUNCT_CHARS:
            tokens.append(Token("punct", ch, ch))
            i += 1
            continue
        if ch in _OP_CHARS:
            j = i + 1
            while j < n and sql[j] in _OP_CHARS and not sql.startswith("--", j) and not sql.startswith("/*", j):
                j += 1
            tokens.append(Token("op", sql[i:j], sql[i:j]))
            i = j
            continue
        raise SqlLexError(f"unexpected character {ch!r} at offset {i}")
    return tokens


def significant_tokens(sql: str) -> list[Token]:
    """Tokens without whitespace and comments."""
    return [t for t in tokenize(sql) if t.kind not in ("ws", "comment")]


def literal_insensitive_fingerprint(sql: str) -> str:
    """SHA-256 of the token stream with literals replaced by ``?`` and words lowercased."""
    parts: list[str] = []
    for tok in significant_tokens(sql):
        if tok.kind in ("string", "number"):
            parts.append("?")
        elif tok.kind == "word":
            parts.append(tok.value)
        elif tok.kind == "qident":
            parts.append('"' + tok.value + '"')
        else:
            parts.append(tok.text)
    return sha256_text(" ".join(parts))


def normalized_sql_hash(sql: str) -> str:
    """SHA-256 of the literal-*sensitive* token stream (whitespace, comments and case removed)."""
    parts: list[str] = []
    for tok in significant_tokens(sql):
        if tok.kind == "word":
            parts.append(tok.value)
        elif tok.kind in ("string", "qident"):
            parts.append(tok.text)
        else:
            parts.append(tok.text)
    return sha256_text(" ".join(parts))


def extract_literals(sql: str) -> list[str]:
    """Literal values (strings and numbers) in source order."""
    return [t.value for t in significant_tokens(sql) if t.kind in ("string", "number")]


_NON_FUNCTION_WORDS = frozenset(
    {
        "select", "from", "where", "in", "exists", "join", "using", "values", "over", "as", "on",
        "and", "or", "not", "when", "then", "else", "case", "having", "by", "any", "all", "some",
        "filter", "within", "into", "with", "materialized", "lateral", "union", "intersect", "except",
    }
)
_AGGREGATE_FUNCTIONS = frozenset(
    {"count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop", "variance",
     "var_samp", "var_pop", "array_agg", "string_agg", "bool_and", "bool_or", "every"}
)

#: Names of the A-class SQL-structure features produced by :func:`sql_structure_features`.
SQL_FEATURE_NAMES: tuple[str, ...] = (
    "sql_length_chars",
    "sql_token_count",
    "sql_literal_count",
    "sql_select_count",
    "sql_subquery_count",
    "sql_join_keyword_count",
    "sql_where_count",
    "sql_exists_count",
    "sql_in_count",
    "sql_not_count",
    "sql_like_count",
    "sql_group_by_count",
    "sql_order_by_count",
    "sql_has_limit",
    "sql_distinct_count",
    "sql_has_cte",
    "sql_set_operation_count",
    "sql_window_count",
    "sql_case_count",
    "sql_and_count",
    "sql_or_count",
    "sql_function_call_count",
    "sql_aggregate_call_count",
)


def sql_structure_features(sql: str) -> dict[str, float]:
    """Availability-class A features computed from SQL text only."""
    toks = significant_tokens(sql)
    words = [t.value if t.kind == "word" else None for t in toks]

    def count_word(word: str) -> int:
        return sum(1 for w in words if w == word)

    def count_pair(first: str, second: str) -> int:
        return sum(1 for a, b in zip(words, words[1:]) if a == first and b == second)

    subqueries = 0
    function_calls = 0
    aggregate_calls = 0
    for idx, tok in enumerate(toks[:-1]):
        nxt = toks[idx + 1]
        if tok.kind == "punct" and tok.text == "(" and nxt.kind == "word" and nxt.value in ("select", "with"):
            subqueries += 1
        if tok.kind == "word" and nxt.kind == "punct" and nxt.text == "(" and tok.value not in _NON_FUNCTION_WORDS:
            function_calls += 1
            if tok.value in _AGGREGATE_FUNCTIONS:
                aggregate_calls += 1

    first_word = next((w for w in words if w is not None), "")
    return {
        "sql_length_chars": float(len(normalize_sql_text(sql))),
        "sql_token_count": float(len(toks)),
        "sql_literal_count": float(sum(1 for t in toks if t.kind in ("string", "number"))),
        "sql_select_count": float(count_word("select")),
        "sql_subquery_count": float(subqueries),
        "sql_join_keyword_count": float(count_word("join")),
        "sql_where_count": float(count_word("where")),
        "sql_exists_count": float(count_word("exists")),
        "sql_in_count": float(count_word("in")),
        "sql_not_count": float(count_word("not")),
        "sql_like_count": float(count_word("like") + count_word("ilike")),
        "sql_group_by_count": float(count_pair("group", "by")),
        "sql_order_by_count": float(count_pair("order", "by")),
        "sql_has_limit": float(1 if count_word("limit") else 0),
        "sql_distinct_count": float(count_word("distinct")),
        "sql_has_cte": float(1 if first_word == "with" else 0),
        "sql_set_operation_count": float(count_word("union") + count_word("intersect") + count_word("except")),
        "sql_window_count": float(count_word("over")),
        "sql_case_count": float(count_word("case")),
        "sql_and_count": float(count_word("and")),
        "sql_or_count": float(count_word("or")),
        "sql_function_call_count": float(function_calls),
        "sql_aggregate_call_count": float(aggregate_calls),
    }
