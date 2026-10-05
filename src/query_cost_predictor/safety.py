"""Single read-only statement validator.

Defense in depth — this validator is one of three independent layers:

1. this text check (single ``SELECT`` / ``WITH ... SELECT`` statement, no
   side-effect keywords or functions);
2. every execution runs in a ``READ ONLY`` transaction that is rolled back;
3. the benchmark role ``qcp_bench_reader`` is not a superuser, has only
   ``SELECT`` privileges and defaults to read-only transactions.

The validator is token-based (see ``sqltext.py``), not a full SQL parser. It
fails closed: anything it cannot tokenize is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from query_cost_predictor.hashing import normalize_sql_text
from query_cost_predictor.sqltext import SqlLexError, Token, tokenize

SAFETY_PASSED = "PASSED_READ_ONLY_SINGLE_STATEMENT"

ALLOWED_FIRST_WORDS = frozenset({"select", "with"})

#: Words that may never appear outside literals, because they could introduce a
#: write, a lock, a second command, transaction control or ``SELECT INTO``.
FORBIDDEN_WORDS = frozenset(
    {
        "insert", "update", "delete", "merge", "into", "returning", "share",
        "copy", "truncate", "create", "alter", "drop", "grant", "revoke",
        "lock", "call", "execute", "prepare", "deallocate", "do", "notify",
        "listen", "unlisten", "vacuum", "analyze", "analyse", "refresh",
        "cluster", "reindex", "discard", "checkpoint", "begin", "commit",
        "rollback", "savepoint", "abort", "security", "import", "explain",
        "declare",
    }
)
# Note: "fetch" is deliberately allowed because FETCH FIRST n ROWS ONLY is a
# read-only SELECT clause; a standalone FETCH statement fails the first-word rule.

#: Functions with side effects, file/network access, sleeping, locking or the
#: ability to run nested SQL text.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file",
        "pg_ls_dir", "pg_stat_file", "pg_ls_logdir", "pg_ls_waldir", "pg_ls_tmpdir",
        "pg_ls_archive_statusdir", "set_config", "pg_terminate_backend", "pg_cancel_backend",
        "pg_reload_conf", "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point",
        "pg_promote", "pg_log_backend_memory_contexts", "txid_current", "pg_current_xact_id",
        "nextval", "setval", "pg_notify", "query_to_xml", "query_to_xml_and_xmlschema",
        "query_to_xmlschema", "cursor_to_xml", "cursor_to_xmlschema", "table_to_xml",
        "table_to_xml_and_xmlschema", "table_to_xmlschema", "schema_to_xml",
        "schema_to_xml_and_xmlschema", "schema_to_xmlschema", "database_to_xml",
        "database_to_xml_and_xmlschema", "database_to_xmlschema", "lowrite", "loread",
        "pg_file_write", "pg_file_rename", "pg_file_unlink", "pg_import_system_collations",
        "pg_backup_start", "pg_backup_stop",
    }
)
FORBIDDEN_FUNCTION_PREFIXES = (
    "pg_advisory", "pg_try_advisory", "lo_", "dblink", "pg_stat_reset", "pg_create_",
    "pg_drop_", "pg_replication_", "pg_logical_", "pg_stat_statements_reset",
)


class SqlSafetyError(ValueError):
    """Raised when SQL is not a single read-only statement."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class SafetyResult:
    status: str
    sql: str  # normalized SQL that will be executed (no trailing semicolon)
    first_word: str
    function_names: tuple[str, ...]


def _function_name_before_paren(tokens: list[Token], index: int) -> str | None:
    """Return the lowercased function name for ``tokens[index] == '('``, if any."""
    if index == 0:
        return None
    prev = tokens[index - 1]
    if prev.kind == "word":
        return prev.value
    if prev.kind == "qident":
        return prev.value.lower()
    return None


def validate_read_only_sql(sql: str) -> SafetyResult:
    """Validate ``sql`` and return the normalized statement, or raise ``SqlSafetyError``."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlSafetyError("EMPTY", "SQL text is empty")
    for ch in sql:
        if ord(ch) < 32 and ch not in "\t\n\r":
            raise SqlSafetyError("CONTROL_CHARACTER", f"control character U+{ord(ch):04X} present")
    normalized = normalize_sql_text(sql)
    try:
        all_tokens = tokenize(normalized)
    except SqlLexError as exc:
        raise SqlSafetyError("UNPARSEABLE", str(exc)) from exc
    tokens = [t for t in all_tokens if t.kind not in ("ws", "comment")]
    if not tokens:
        raise SqlSafetyError("EMPTY", "SQL contains only comments or whitespace")

    semicolons = [t for t in tokens if t.kind == "semicolon"]
    if semicolons:
        raise SqlSafetyError("MULTIPLE_STATEMENTS", "a semicolon remains after removing one trailing semicolon")
    for tok in tokens:
        if tok.kind == "param":
            raise SqlSafetyError("POSITIONAL_PARAMETER", f"{tok.text} found; literals must be rendered")
        if tok.kind == "punct" and tok.text == "\\":
            raise SqlSafetyError("META_COMMAND", "backslash outside literals (psql meta-command?)")

    first = tokens[0]
    if first.kind != "word" or first.value not in ALLOWED_FIRST_WORDS:
        raise SqlSafetyError("NOT_SELECT", f"statement must start with SELECT or WITH, found {first.text!r}")

    for tok in tokens:
        if tok.kind == "word" and tok.value in FORBIDDEN_WORDS:
            raise SqlSafetyError("FORBIDDEN_KEYWORD", f"keyword {tok.value.upper()} is not allowed")

    functions: list[str] = []
    for idx, tok in enumerate(tokens):
        if tok.kind == "punct" and tok.text == "(":
            name = _function_name_before_paren(tokens, idx)
            if name is None:
                continue
            functions.append(name)
            if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise SqlSafetyError("FORBIDDEN_FUNCTION", f"function {name}() is not allowed")

    if first.value == "with" and not any(t.kind == "word" and t.value == "select" for t in tokens):
        raise SqlSafetyError("NOT_SELECT", "WITH statement without SELECT")

    return SafetyResult(
        status=SAFETY_PASSED,
        sql=normalized,
        first_word=first.value,
        function_names=tuple(sorted(set(functions))),
    )
