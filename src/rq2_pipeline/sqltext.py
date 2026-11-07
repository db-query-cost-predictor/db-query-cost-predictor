"""SQL lexical primitives used by safety checks and fixture binding."""
from __future__ import annotations
import re
from dataclasses import dataclass

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
        if sql[i:i + 3].lower() == 'u&"':
            # PostgreSQL decodes these names before function/relation lookup.
            # Reject syntax this lexer cannot decode rather than inspecting an alias.
            raise SqlLexError("Unicode-escaped identifiers are unsupported")
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
