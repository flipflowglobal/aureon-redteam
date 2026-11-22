"""Fail on placeholder, elided and toy code that compilers and linters accept.

This is the repository-wide gate of the Zero-Toy-Code standard. It reads
source and configuration files and reports:

=====  ============================  ==========================================
Code   Where                         What
=====  ============================  ==========================================
ZT001  every scanned file            upper-case unfinished-work tag
ZT002  comments                      elided code (leading ellipsis, "rest of")
ZT003  comments and docstrings       toy or hedging phrase
ZT004  production code only          test-double vocabulary
ZT005  string literals               placeholder text
ZT006  comments, Rust attributes     coverage-exclusion pragma
ZT010  Python                        function body that is only a placeholder
ZT011  Python                        ``pass`` or stray ``...`` statement
ZT012  Python                        any use of ``NotImplementedError``
ZT013  Rust code                     ``todo!`` or ``unimplemented!``
ZT900  any scanned file              file cannot be parsed
ZT901  any scanned file              file cannot be read as UTF-8
=====  ============================  ==========================================

Python files are parsed with :mod:`ast` and :mod:`tokenize`, so Python
results are exact. Rust, Solidity, JavaScript, TypeScript and JSON files go
through a lexer that separates comments, string literals and code. Two
documented approximations apply there: a JavaScript regular-expression
literal is read as code, and a ``${}`` expression inside a template literal
is read as string text. Shell, TOML, YAML, Dockerfile and Makefile comments
start at a ``#`` outside quotes that begins the line or follows whitespace.

Test-only code is any path inside a ``tests``, ``test``, ``__tests__`` or
``benches`` directory; any file named ``test_*.py``, ``*_test.py``,
``conftest.py``, ``*.t.sol``, ``tests.rs``, ``*.test.*`` or ``*.spec.*``;
any Rust item under ``#[cfg(test)]``; and any Rust file with
``#![cfg(test)]``.

Usage::

    python3 tools/zero_toy_scan.py               # whole git work tree
    python3 tools/zero_toy_scan.py PATH [PATH]   # files or directories

With no paths, the scanner reads every tracked file plus every untracked
file that ``.gitignore`` does not exclude, so new files are checked before
they are committed. Generated lockfiles and minified JavaScript are skipped.

Exit status: 0 clean, 1 findings, 2 the scan could not run.

Cost: time O(total bytes x patterns), memory O(largest file).
"""

from __future__ import annotations

import argparse
import ast
import bisect
import fnmatch
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tokenize
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import TextIO

LOGGER: Final = logging.getLogger("zero_toy_scan")

EXIT_CLEAN: Final = 0
EXIT_FINDINGS: Final = 1
EXIT_ERROR: Final = 2

_DETAIL_LIMIT: Final = 120


class Rule(Enum):
    """Rule identifiers reported by the scanner."""

    UNFINISHED_MARKER = "ZT001"
    ELIDED_CODE = "ZT002"
    TOY_PHRASE = "ZT003"
    TEST_DOUBLE_IN_PRODUCTION = "ZT004"
    PLACEHOLDER_STRING = "ZT005"
    COVERAGE_EXCLUSION = "ZT006"
    PLACEHOLDER_BODY = "ZT010"
    PLACEHOLDER_STATEMENT = "ZT011"
    NOT_IMPLEMENTED_ERROR = "ZT012"
    PLACEHOLDER_MACRO = "ZT013"
    PARSE_ERROR = "ZT900"
    READ_ERROR = "ZT901"


_RULE_MESSAGES: Final[dict[Rule, str]] = {
    Rule.UNFINISHED_MARKER: (
        "unfinished-work tag: finish the work or list it in the report"
    ),
    Rule.ELIDED_CODE: "elided code: write the complete file",
    Rule.TOY_PHRASE: "toy or hedging phrase: implement it or remove it",
    Rule.TEST_DOUBLE_IN_PRODUCTION: "test-double vocabulary in production code",
    Rule.PLACEHOLDER_STRING: "placeholder text in a string literal",
    Rule.COVERAGE_EXCLUSION: "coverage exclusion: test the code instead",
    Rule.PLACEHOLDER_BODY: "function body is only a placeholder",
    Rule.PLACEHOLDER_STATEMENT: "placeholder statement: write real logic or delete it",
    Rule.NOT_IMPLEMENTED_ERROR: (
        "NotImplementedError: implement it, or declare an @abstractmethod "
        "with a docstring-only body"
    ),
    Rule.PLACEHOLDER_MACRO: "placeholder macro: implement it",
    Rule.PARSE_ERROR: "file cannot be parsed",
    Rule.READ_ERROR: "file cannot be read as UTF-8 text",
}

# Character classes keep this file from matching its own ZT001 pattern.
_MARKER_RE: Final = re.compile(r"\b(?:T[O]DO|F[I]XME|H[A]CK|X[X]X|T[B]D)\b")

_ELLIPSIS_START_RE: Final = re.compile(r"^(?:\.{3}|…|\.\s\.\s\.)")
_ELISION_RE: Final = re.compile(
    r"\b(?:(?:rest|remainder)\s+of\s+(?:the\s+)?"
    r"(?:code|file|function|implementation|class|module|logic|method|body|contract|tests?)"
    r"|(?:code|logic|implementation|body|contents?)\s+"
    r"(?:unchanged|omitted|elided|truncated|abbreviated)"
    r"|(?:unchanged|omitted|elided|truncated)\s+for\s+brevity"
    r"|same\s+as\s+(?:before|above|the\s+original))\b",
    re.IGNORECASE,
)
_TOY_RE: Final = re.compile(
    r"\bin\s+an?\s+(?:real|production|full|complete|actual|proper|live)\s+"
    r"(?:implementation|system|application|app|scenario|setting|environment"
    r"|deployment|version|world|codebase|project|integration)\b"
    r"|\bin\s+production,?\s+(?:you|we|this|it|one)\s+(?:would|should|could|might)\b"
    r"|\bfor\s+(?:demo|demonstration|illustration|illustrative|example|educational"
    r"|tutorial)\s+purposes\b"
    r"|\b(?:this|here)\s+is\s+(?:just\s+|only\s+|merely\s+)?an?\s+"
    r"(?:simplified|toy|naive|dummy|mock|fake|placeholder)\b"
    r"|\bsimplified\s+(?:version|implementation|example|logic|stand-?in)\b"
    r"|\b(?:implement|add|insert|put)\s+(?:your|the\s+real|the\s+actual|real|actual"
    r"|proper)\s+(?:logic|implementation|code)\b"
    r"|\b(?:your|real|actual)\s+(?:code|logic|implementation)\s+(?:goes\s+)?here\b"
    r"|\b(?:implementation|logic|code)\s+goes\s+here\b"
    r"|\bnot\s+(?:yet\s+implemented|implemented\s+yet)\b"
    r"|\bto\s+be\s+(?:implemented|completed|determined)\b"
    r"|\b(?:implement|fill\s+in|flesh\s+out|finish)\s+(?:this\s+|it\s+|me\s+)?"
    r"(?:later|eventually)\b"
    r"|\bleft\s+as\s+an\s+exercise\b"
    r"|\bfor\s+now\b"
    r"|\btemporary\s+(?:fix|hack|workaround|solution|implementation|code)\b"
    r"|\bquick\s+(?:fix|hack)\b"
    r"|\b(?:would|should)\s+(?:normally|typically|usually)\s+"
    r"(?:call|fetch|query|connect|use|read|load|send)\b"
    r"|\breplace\s+(?:this|me|with\s+(?:a\s+|the\s+)?(?:real|actual|proper))\b",
    re.IGNORECASE,
)
_TEST_DOUBLE_ONLY_RE: Final = re.compile(
    r"^(?:placeholder|stub|stubbed|mock|mocked|dummy|fake|sample)"
    r"(?:\s+(?:implementation|impl|data|values?|response|code|logic|function|method"
    r"|version|object|class))?\W*$",
    re.IGNORECASE,
)
_TEST_DOUBLE_PHRASE_RE: Final = re.compile(
    r"\b(?:mock|mocked|stubbed)\s+(?:data|values?|responses?|prices?|balances?"
    r"|results?|implementation|reserves|quotes?|address(?:es)?|keys?|rpc|provider"
    r"|exchange|pool|oracle|feed|client)\b",
    re.IGNORECASE,
)
_PLACEHOLDER_STRING_RE: Final = re.compile(
    r"^\s*(?:not\s+(?:yet\s+)?implemented|implement\s+me|coming\s+soon|lorem\s+ipsum)\b",
    re.IGNORECASE,
)
_COVERAGE_PRAGMA_RE: Final = re.compile(
    r"pragma:\s*no\s*(?:cover|branch)|\b(?:istanbul|c8|v8)\s+ignore\b"
    r"|LCOV_EXCL|coverage:\s*ignore",
    re.IGNORECASE,
)
_RUST_COVERAGE_ATTRIBUTE_RE: Final = re.compile(
    r"#\s*!?\s*\[\s*coverage\s*\(\s*off\s*\)"
)
_RUST_PLACEHOLDER_MACRO_RE: Final = re.compile(r"\b(?:todo|unimplemented)\s*!")
_RUST_CFG_TEST_OUTER_RE: Final = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
_RUST_CFG_TEST_INNER_RE: Final = re.compile(r"#\s*!\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
_RUST_RAW_STRING_RE: Final = re.compile(r'(?:br|cr|r)(#*)"')
_RAW_PREFIX_CHARS: Final = frozenset("brc")

_EXEMPT_DECORATORS: Final = frozenset({"abstractmethod", "overload"})
_PROTOCOL: Final = "Protocol"
_NOT_IMPLEMENTED_ERROR: Final = "NotImplementedError"
_FUNCTION_NODES: Final = (ast.FunctionDef, ast.AsyncFunctionDef)
_DOCSTRING_OWNERS: Final = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)

_TEST_DIRECTORIES: Final = frozenset({"tests", "test", "__tests__", "benches"})
_TEST_FILE_GLOBS: Final = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.t.sol",
    "tests.rs",
    "*.test.*",
    "*.spec.*",
)
_EXCLUDED_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".next",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "cache",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "out",
        "target",
        "typechain-types",
        "venv",
    },
)
_SKIPPED_NAMES: Final = frozenset(
    {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml"}
)
_SKIPPED_SUFFIXES: Final = (".min.js", ".min.mjs", ".min.cjs")


class Language(Enum):
    """Source languages the scanner understands."""

    PYTHON = "python"
    PYTHON_STUB = "python-stub"
    RUST = "rust"
    SOLIDITY = "solidity"
    JAVASCRIPT = "javascript"
    JSON = "json"
    HASH_COMMENT = "hash-comment"


_SUFFIX_LANGUAGES: Final[dict[str, Language]] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON_STUB,
    ".rs": Language.RUST,
    ".sol": Language.SOLIDITY,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.JAVASCRIPT,
    ".tsx": Language.JAVASCRIPT,
    ".mts": Language.JAVASCRIPT,
    ".cts": Language.JAVASCRIPT,
    ".json": Language.JSON,
    ".jsonc": Language.JSON,
    ".sh": Language.HASH_COMMENT,
    ".bash": Language.HASH_COMMENT,
    ".zsh": Language.HASH_COMMENT,
    ".toml": Language.HASH_COMMENT,
    ".yml": Language.HASH_COMMENT,
    ".yaml": Language.HASH_COMMENT,
    ".mk": Language.HASH_COMMENT,
    ".dockerfile": Language.HASH_COMMENT,
}
_NAME_LANGUAGES: Final[dict[str, Language]] = {
    "Dockerfile": Language.HASH_COMMENT,
    "Makefile": Language.HASH_COMMENT,
    "makefile": Language.HASH_COMMENT,
    "GNUmakefile": Language.HASH_COMMENT,
}


@dataclass(frozen=True, slots=True)
class Finding:
    """A rule violation at one line of one file."""

    path: str
    line: int
    rule: Rule
    detail: str

    def render(self) -> str:
        """Return the finding as ``path:line: CODE message: detail``."""
        message = _RULE_MESSAGES[self.rule]
        return f"{self.path}:{self.line}: {self.rule.value} {message}: {self.detail}"


@dataclass(frozen=True, slots=True)
class TextSpan:
    """Text taken from a source file, with the 1-based line it starts on."""

    line: int
    text: str


@dataclass(frozen=True, slots=True)
class LexResult:
    """Comments, string literals and code of one C-family source file.

    ``code_mask`` has the same length as the source. Every character inside a
    comment or string literal is replaced by a space and newlines are kept, so
    offsets and line numbers in the mask match the source.
    """

    comments: tuple[TextSpan, ...]
    strings: tuple[TextSpan, ...]
    code_mask: str


@dataclass(frozen=True, slots=True)
class CDialect:
    """Lexical features that differ between C-family languages."""

    nested_block_comments: bool
    single_quote_strings: bool
    template_literals: bool
    multiline_strings: bool
    rust_literals: bool


DIALECTS: Final[dict[Language, CDialect]] = {
    Language.RUST: CDialect(
        nested_block_comments=True,
        single_quote_strings=False,
        template_literals=False,
        multiline_strings=True,
        rust_literals=True,
    ),
    Language.SOLIDITY: CDialect(
        nested_block_comments=False,
        single_quote_strings=True,
        template_literals=False,
        multiline_strings=False,
        rust_literals=False,
    ),
    Language.JAVASCRIPT: CDialect(
        nested_block_comments=False,
        single_quote_strings=True,
        template_literals=True,
        multiline_strings=False,
        rust_literals=False,
    ),
    Language.JSON: CDialect(
        nested_block_comments=False,
        single_quote_strings=False,
        template_literals=False,
        multiline_strings=False,
        rust_literals=False,
    ),
}


@dataclass(frozen=True, slots=True)
class SourceFile:
    """A file selected for scanning."""

    path: Path
    display: PurePosixPath
    language: Language


@dataclass(frozen=True, slots=True)
class _Scope:
    """Which lines of a file are production code."""

    test_file: bool
    test_ranges: tuple[tuple[int, int], ...] = ()

    def is_production(self, line: int) -> bool:
        if self.test_file:
            return False
        return not any(start <= line <= end for start, end in self.test_ranges)


@dataclass(frozen=True, slots=True)
class _PythonTexts:
    """Comment, docstring and other string-literal text of a Python module."""

    comments: tuple[TextSpan, ...]
    docstrings: tuple[TextSpan, ...]
    strings: tuple[TextSpan, ...]


class ScanError(Exception):
    """The scan cannot run, for example because a path does not exist."""


class _LineIndex:
    """Maps character offsets to 1-based line numbers in O(log n) per lookup."""

    def __init__(self, text: str) -> None:
        self._starts = [0, *(match.end() for match in re.finditer("\n", text))]

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)


class _CLexer:
    """Single-pass lexer for C-family comments and string literals.

    Every source character is visited a constant number of times, so a file
    of n characters is lexed in O(n) time and O(n) memory.
    """

    def __init__(self, source: str, dialect: CDialect) -> None:
        self._source = source
        self._dialect = dialect
        self._mask = list(source)
        self._lines = _LineIndex(source)
        self._comments: list[TextSpan] = []
        self._strings: list[TextSpan] = []

    def run(self) -> LexResult:
        position = 0
        length = len(self._source)
        while position < length:
            position = self._advance(position)
        return LexResult(
            comments=tuple(self._comments),
            strings=tuple(self._strings),
            code_mask="".join(self._mask),
        )

    def _advance(self, position: int) -> int:
        pair = self._source[position : position + 2]
        if pair == "//":
            return self._line_comment(position)
        if pair == "/*":
            return self._block_comment(position)
        char = self._source[position]
        if self._dialect.rust_literals:
            return self._advance_rust(position, char)
        if char == '"' or (char == "'" and self._dialect.single_quote_strings):
            return self._quoted(
                position, char, multiline=self._dialect.multiline_strings
            )
        if char == "`" and self._dialect.template_literals:
            return self._quoted(position, char, multiline=True)
        return position + 1

    def _advance_rust(self, position: int, char: str) -> int:
        raw_end = self._raw_string(position)
        if raw_end is not None:
            return raw_end
        if char == '"':
            return self._quoted(position, char, multiline=True)
        if char == "'":
            return self._char_or_lifetime(position)
        return position + 1

    def _line_comment(self, position: int) -> int:
        newline = self._source.find("\n", position)
        stop = len(self._source) if newline == -1 else newline
        line = self._lines.line_of(position)
        self._add_comment(line, self._source[position + 2 : stop].lstrip("/!"))
        self._blank(position, stop)
        return stop

    def _block_comment(self, position: int) -> int:
        source = self._source
        length = len(source)
        depth = 1
        cursor = position + 2
        while cursor < length and depth > 0:
            pair = source[cursor : cursor + 2]
            if pair == "*/":
                depth -= 1
                cursor += 2
            elif pair == "/*" and self._dialect.nested_block_comments:
                depth += 1
                cursor += 2
            else:
                cursor += 1
        body_end = cursor - 2 if depth == 0 else cursor
        first_line = self._lines.line_of(position)
        body = source[position + 2 : body_end]
        for offset, raw_line in enumerate(body.split("\n")):
            self._add_comment(first_line + offset, raw_line.strip().lstrip("*!"))
        self._blank(position, cursor)
        return cursor

    def _quoted(self, position: int, quote: str, *, multiline: bool) -> int:
        source = self._source
        length = len(source)
        cursor = position + 1
        while cursor < length:
            char = source[cursor]
            if char == "\\":
                cursor += 2
            elif char == quote:
                self._add_string(position, source[position + 1 : cursor])
                self._blank(position, cursor + 1)
                return cursor + 1
            elif char == "\n" and not multiline:
                break
            else:
                cursor += 1
        stop = min(cursor, length)
        self._add_string(position, source[position + 1 : stop])
        self._blank(position, stop)
        return stop

    def _raw_string(self, position: int) -> int | None:
        source = self._source
        if source[position] not in _RAW_PREFIX_CHARS:
            return None
        if position > 0 and _is_identifier_char(source[position - 1]):
            return None
        match = _RUST_RAW_STRING_RE.match(source, position)
        if match is None:
            return None
        terminator = '"' + match.group(1)
        close = source.find(terminator, match.end())
        content_end = len(source) if close == -1 else close
        stop = len(source) if close == -1 else close + len(terminator)
        self._add_string(position, source[match.end() : content_end])
        self._blank(position, stop)
        return stop

    def _char_or_lifetime(self, position: int) -> int:
        source = self._source
        if source.startswith("\\", position + 1):
            close = source.find("'", position + 3)
            if close == -1:
                return position + 1
            self._blank(position, close + 1)
            return close + 1
        if source.startswith("'", position + 2) and source[position + 1] != "\n":
            self._blank(position, position + 3)
            return position + 3
        return position + 1

    def _add_comment(self, line: int, text: str) -> None:
        stripped = text.strip()
        if stripped:
            self._comments.append(TextSpan(line, stripped))

    def _add_string(self, position: int, text: str) -> None:
        if text:
            self._strings.append(TextSpan(self._lines.line_of(position), text))

    def _blank(self, start: int, stop: int) -> None:
        for index in range(start, stop):
            if self._mask[index] != "\n":
                self._mask[index] = " "


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def lex_c_family(source: str, dialect: CDialect) -> LexResult:
    """Split C-family source into comments, string literals and a code mask.

    Args:
        source: Complete file text.
        dialect: Lexical features of the file's language.

    Returns:
        The comments and string literals with their lines, and a code mask.
    """
    return _CLexer(source, dialect).run()


def hash_comments(source: str) -> tuple[TextSpan, ...]:
    """Return ``#`` comments from shell, TOML, YAML, Dockerfile or Makefile text.

    Args:
        source: Complete file text.

    Returns:
        One span per comment, with the comment marker removed.
    """
    starts = (
        (number, line, _hash_comment_start(line))
        for number, line in enumerate(source.split("\n"), start=1)
    )
    texts = (
        (number, line[start:].lstrip("#!").strip())
        for number, line, start in starts
        if start is not None
    )
    return tuple(TextSpan(number, text) for number, text in texts if text)


def _hash_comment_start(line: str) -> int | None:
    quote = ""
    escaped = False
    previous = " "
    for index, char in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and previous.isspace():
            return index
        previous = char
    return None


def language_for(path: PurePosixPath) -> Language | None:
    """Return the language used to scan ``path``.

    Args:
        path: Path of the file, relative to the scan root.

    Returns:
        The language, or ``None`` when the file is not scanned.
    """
    name = path.name
    if name in _SKIPPED_NAMES or name.endswith(_SKIPPED_SUFFIXES):
        return None
    if name in _NAME_LANGUAGES:
        return _NAME_LANGUAGES[name]
    return _SUFFIX_LANGUAGES.get(path.suffix.lower())


def is_test_path(path: PurePosixPath) -> bool:
    """Return whether ``path`` holds test-only code, judged by location and name.

    Args:
        path: Path of the file, relative to the scan root.

    Returns:
        ``True`` for test-only files.
    """
    if _TEST_DIRECTORIES.intersection(path.parts[:-1]):
        return True
    return any(fnmatch.fnmatchcase(path.name, pattern) for pattern in _TEST_FILE_GLOBS)


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _DETAIL_LIMIT:
        return collapsed
    return collapsed[: _DETAIL_LIMIT - 3] + "..."


def _has_test_double_vocabulary(text: str) -> bool:
    stripped = text.strip()
    return (
        _TEST_DOUBLE_ONLY_RE.match(stripped) is not None
        or _TEST_DOUBLE_PHRASE_RE.search(stripped) is not None
    )


def _marker_findings(path: str, source: str) -> list[Finding]:
    return [
        Finding(path, number, Rule.UNFINISHED_MARKER, _excerpt(line))
        for number, line in enumerate(source.split("\n"), start=1)
        if _MARKER_RE.search(line)
    ]


def _comment_findings(path: str, span: TextSpan) -> list[Finding]:
    text = span.text
    checks = (
        (
            Rule.ELIDED_CODE,
            _ELLIPSIS_START_RE.match(text) is not None
            or _ELISION_RE.search(text) is not None,
        ),
        (Rule.TOY_PHRASE, _TOY_RE.search(text) is not None),
        (Rule.COVERAGE_EXCLUSION, _COVERAGE_PRAGMA_RE.search(text) is not None),
    )
    return [
        Finding(path, span.line, rule, _excerpt(text)) for rule, hit in checks if hit
    ]


def _text_findings(
    path: str,
    *,
    comments: Sequence[TextSpan],
    docstrings: Sequence[TextSpan],
    strings: Sequence[TextSpan],
    scope: _Scope,
) -> list[Finding]:
    findings = [
        finding for span in comments for finding in _comment_findings(path, span)
    ]
    findings.extend(
        Finding(path, span.line, Rule.TOY_PHRASE, _excerpt(span.text))
        for span in docstrings
        if _TOY_RE.search(span.text)
    )
    findings.extend(
        Finding(path, span.line, Rule.PLACEHOLDER_STRING, _excerpt(span.text))
        for span in strings
        if _PLACEHOLDER_STRING_RE.match(span.text)
    )
    findings.extend(
        Finding(path, span.line, Rule.TEST_DOUBLE_IN_PRODUCTION, _excerpt(span.text))
        for span in (*comments, *docstrings, *strings)
        if scope.is_production(span.line) and _has_test_double_vocabulary(span.text)
    )
    return findings


def _python_comments(source: str) -> tuple[TextSpan, ...]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    texts = (
        (token.start[0], token.string.lstrip("#!").strip())
        for token in tokens
        if token.type == tokenize.COMMENT
    )
    return tuple(TextSpan(line, text) for line, text in texts if text)


def _docstring_node(node: ast.AST) -> ast.Constant | None:
    if not isinstance(node, _DOCSTRING_OWNERS) or not node.body:
        return None
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value
    return None


def _python_texts(tree: ast.Module, comments: tuple[TextSpan, ...]) -> _PythonTexts:
    docstring_nodes = [doc for node in ast.walk(tree) if (doc := _docstring_node(node))]
    docstring_ids = {id(node) for node in docstring_nodes}
    docstrings = tuple(
        TextSpan(node.lineno + offset, line.strip())
        for node in docstring_nodes
        for offset, line in enumerate(str(node.value).split("\n"))
        if line.strip()
    )
    strings = tuple(
        TextSpan(node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value
        and id(node) not in docstring_ids
    )
    return _PythonTexts(comments=comments, docstrings=docstrings, strings=strings)


def _terminal_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_protocol_class(node: ast.ClassDef) -> bool:
    return any(
        _terminal_name(base.value if isinstance(base, ast.Subscript) else base)
        == _PROTOCOL
        for base in node.bases
    )


def _protocol_member_ids(tree: ast.Module) -> set[int]:
    return {
        id(item)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _is_protocol_class(node)
        for item in node.body
        if isinstance(item, _FUNCTION_NODES)
    }


def _is_exempt_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    protocol_members: set[int],
) -> bool:
    if id(node) in protocol_members:
        return True
    return any(
        _terminal_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
        in _EXEMPT_DECORATORS
        for decorator in node.decorator_list
    )


def _is_docstring_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_ellipsis_statement(statement: ast.AST) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _is_placeholder_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass) or _is_ellipsis_statement(statement):
        return True
    if isinstance(statement, ast.Return):
        value = statement.value
        return value is None or (
            isinstance(value, ast.Constant) and value.value is None
        )
    if isinstance(statement, ast.Raise) and statement.exc is not None:
        exception = statement.exc
        target = exception.func if isinstance(exception, ast.Call) else exception
        return _terminal_name(target) == _NOT_IMPLEMENTED_ERROR
    return False


def _is_placeholder_body(body: list[ast.stmt]) -> bool:
    statements = body[1:] if body and _is_docstring_statement(body[0]) else body
    return not statements or (
        len(statements) == 1 and _is_placeholder_statement(statements[0])
    )


def _not_implemented_findings(path: str, tree: ast.Module) -> list[Finding]:
    lines = sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == _NOT_IMPLEMENTED_ERROR)
            or (isinstance(node, ast.Attribute) and node.attr == _NOT_IMPLEMENTED_ERROR)
        },
    )
    return [
        Finding(path, line, Rule.NOT_IMPLEMENTED_ERROR, _NOT_IMPLEMENTED_ERROR)
        for line in lines
    ]


def _python_ast_findings(path: str, tree: ast.Module) -> list[Finding]:
    protocol_members = _protocol_member_ids(tree)
    tolerated: set[int] = set()
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, _FUNCTION_NODES):
            continue
        if _is_exempt_function(node, protocol_members):
            tolerated.update(
                id(item) for item in node.body if _is_ellipsis_statement(item)
            )
        elif _is_placeholder_body(node.body):
            findings.append(
                Finding(path, node.lineno, Rule.PLACEHOLDER_BODY, f"def {node.name}")
            )
            tolerated.update(id(item) for item in node.body)
    findings.extend(
        Finding(
            path,
            node.lineno,
            Rule.PLACEHOLDER_STATEMENT,
            "pass" if isinstance(node, ast.Pass) else "...",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and id(node) not in tolerated
        and (isinstance(node, ast.Pass) or _is_ellipsis_statement(node))
    )
    findings.extend(_not_implemented_findings(path, tree))
    return findings


def _parse_python(path: str, source: str) -> tuple[ast.Module, tuple[TextSpan, ...]]:
    # Invalid escape sequences in the scanned file warn while parsing
    # (DeprecationWarning before Python 3.12, SyntaxWarning from 3.12). Ruff
    # W605 reports them, so the scanner keeps its output to its own rules.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source, filename=path)
    return tree, _python_comments(source)


def _scan_python(path: str, source: str, *, scope: _Scope, stub: bool) -> list[Finding]:
    try:
        tree, comments = _parse_python(path, source)
    except (SyntaxError, ValueError, tokenize.TokenError) as error:
        line = error.lineno if isinstance(error, SyntaxError) and error.lineno else 1
        return [Finding(path, line, Rule.PARSE_ERROR, _excerpt(str(error)))]
    texts = _python_texts(tree, comments)
    findings = _text_findings(
        path,
        comments=texts.comments,
        docstrings=texts.docstrings,
        strings=texts.strings,
        scope=scope,
    )
    if not stub:
        findings.extend(_python_ast_findings(path, tree))
    return findings


def _matching_brace(mask: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(mask)):
        char = mask[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(mask)


def _rust_item_end(mask: str, start: int) -> int:
    nesting = 0
    for index in range(start, len(mask)):
        char = mask[index]
        if char in "([":
            nesting += 1
        elif char in ")]":
            nesting = max(nesting - 1, 0)
        elif nesting == 0 and char == ";":
            return index + 1
        elif nesting == 0 and char == "{":
            return _matching_brace(mask, index)
    return len(mask)


def _rust_test_ranges(mask: str) -> tuple[tuple[int, int], ...]:
    lines = _LineIndex(mask)
    if _RUST_CFG_TEST_INNER_RE.search(mask):
        return ((1, lines.line_of(len(mask))),)
    return tuple(
        (
            lines.line_of(match.start()),
            lines.line_of(max(_rust_item_end(mask, match.end()) - 1, match.start())),
        )
        for match in _RUST_CFG_TEST_OUTER_RE.finditer(mask)
    )


def _rust_code_findings(path: str, source: str, mask: str) -> list[Finding]:
    findings: list[Finding] = []
    rows = zip(source.split("\n"), mask.split("\n"), strict=True)
    for number, (original, masked) in enumerate(rows, start=1):
        if _RUST_PLACEHOLDER_MACRO_RE.search(masked):
            findings.append(
                Finding(path, number, Rule.PLACEHOLDER_MACRO, _excerpt(original))
            )
        if _RUST_COVERAGE_ATTRIBUTE_RE.search(masked):
            findings.append(
                Finding(path, number, Rule.COVERAGE_EXCLUSION, _excerpt(original))
            )
    return findings


def _scan_c_family(
    path: str, source: str, language: Language, *, test_file: bool
) -> list[Finding]:
    lexed = lex_c_family(source, DIALECTS[language])
    is_rust = language is Language.RUST
    ranges = _rust_test_ranges(lexed.code_mask) if is_rust else ()
    findings = _text_findings(
        path,
        comments=lexed.comments,
        docstrings=(),
        strings=lexed.strings,
        scope=_Scope(test_file=test_file, test_ranges=ranges),
    )
    if is_rust:
        findings.extend(_rust_code_findings(path, source, lexed.code_mask))
    return findings


def _finding_key(finding: Finding) -> tuple[str, int, str, str]:
    return (finding.path, finding.line, finding.rule.value, finding.detail)


def scan_text(display: PurePosixPath, language: Language, source: str) -> list[Finding]:
    """Scan the text of one file.

    Args:
        display: Path of the file relative to the scan root, used in findings
            and to decide whether the file is test-only code.
        language: Language used to read the file.
        source: Complete file text.

    Returns:
        Findings sorted by line, rule and detail.
    """
    path = display.as_posix()
    test_file = is_test_path(display)
    findings = _marker_findings(path, source)
    if language in {Language.PYTHON, Language.PYTHON_STUB}:
        findings.extend(
            _scan_python(
                path,
                source,
                scope=_Scope(test_file=test_file),
                stub=language is Language.PYTHON_STUB,
            ),
        )
    elif language is Language.HASH_COMMENT:
        findings.extend(
            _text_findings(
                path,
                comments=hash_comments(source),
                docstrings=(),
                strings=(),
                scope=_Scope(test_file=test_file),
            ),
        )
    else:
        findings.extend(_scan_c_family(path, source, language, test_file=test_file))
    return sorted(findings, key=_finding_key)


def scan_file(source_file: SourceFile) -> list[Finding]:
    """Read and scan one file.

    Args:
        source_file: The file to scan.

    Returns:
        Findings for the file. A file that cannot be read or decoded yields a
        single ``ZT901`` finding, because an unread file is an unverified file.
    """
    path = source_file.display.as_posix()
    try:
        source = source_file.path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        return [Finding(path, 1, Rule.READ_ERROR, _excerpt(str(error)))]
    return scan_text(source_file.display, source_file.language, source)


def _run_git(git: str, arguments: Sequence[str], *, cwd: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv; executable resolved by shutil.which
        [git, *arguments],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if completed.returncode != 0:
        message = f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        raise ScanError(message)
    return completed.stdout


def _git_candidates() -> list[tuple[Path, PurePosixPath]]:
    git = shutil.which("git")
    if git is None:
        message = "git is not installed; pass the paths to scan explicitly"
        raise ScanError(message)
    root = Path(_run_git(git, ["rev-parse", "--show-toplevel"], cwd=Path.cwd()).strip())
    listing = _run_git(
        git,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    )
    names = sorted({name for name in listing.split("\0") if name})
    return [(root / name, PurePosixPath(name)) for name in names]


def _display(path: Path) -> PurePosixPath:
    return PurePosixPath(Path(os.path.relpath(path)).as_posix())


def _walk(root: Path) -> Iterator[tuple[Path, PurePosixPath]]:
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in _EXCLUDED_DIRECTORIES
        )
        for name in sorted(files):
            path = Path(directory) / name
            yield path, _display(path)


def _path_candidates(paths: Sequence[str]) -> list[tuple[Path, PurePosixPath]]:
    candidates: list[tuple[Path, PurePosixPath]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates.extend(_walk(path))
        elif path.exists():
            candidates.append((path, _display(path)))
        else:
            message = f"path does not exist: {raw}"
            raise ScanError(message)
    return candidates


def _select(
    path: Path, display: PurePosixPath, excludes: Sequence[str]
) -> SourceFile | None:
    text = display.as_posix()
    if any(fnmatch.fnmatchcase(text, pattern) for pattern in excludes):
        return None
    language = language_for(display)
    if language is None or path.is_symlink() or not path.is_file():
        return None
    return SourceFile(path=path, display=display, language=language)


def discover(paths: Sequence[str], excludes: Sequence[str]) -> list[SourceFile]:
    """Return the files to scan, sorted by display path.

    Args:
        paths: Files or directories. Empty means the whole git work tree.
        excludes: Glob patterns matched against display paths; matches are
            skipped. Directory walks also skip build, cache, dependency and
            virtual-environment directories.

    Returns:
        The files to scan. Symbolic links and unsupported file types are
        skipped.

    Raises:
        ScanError: A path does not exist, git is missing, or a git command
            fails.
    """
    candidates = _path_candidates(paths) if paths else _git_candidates()
    selected = (_select(path, display, excludes) for path, display in candidates)
    return sorted(
        (item for item in selected if item is not None),
        key=lambda item: item.display.as_posix(),
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser.

    Returns:
        A parser for ``[PATH ...] [--exclude GLOB ...]``.
    """
    parser = argparse.ArgumentParser(
        prog="zero_toy_scan.py",
        description="Fail on placeholder, elided and toy code.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="files or directories to scan (default: the whole git work tree)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip display paths matching GLOB, for example 'lib/*' (repeatable)",
    )
    return parser


def _write_findings(stream: TextIO, findings: Sequence[Finding]) -> None:
    stream.writelines(f"{finding.render()}\n" for finding in findings)


def _report_failure(error: ScanError) -> int:
    LOGGER.error("scan could not run: %s", error)
    return EXIT_ERROR


def _summarize(findings: Sequence[Finding], scanned: int) -> int:
    if findings:
        files = len({finding.path for finding in findings})
        LOGGER.error("%d finding(s) in %d of %d file(s)", len(findings), files, scanned)
        return EXIT_FINDINGS
    LOGGER.info("clean: %d file(s) scanned", scanned)
    return EXIT_CLEAN


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scanner.

    Args:
        argv: Command-line arguments without the program name. ``None`` reads
            ``sys.argv``.

    Returns:
        0 when clean, 1 when there are findings, 2 when the scan cannot run.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(name)s: %(levelname)s: %(message)s"
    )
    arguments = build_parser().parse_args(argv)
    paths: list[str] = arguments.paths
    excludes: list[str] = arguments.exclude
    try:
        files = discover(paths, excludes)
    except ScanError as error:
        return _report_failure(error)
    findings = [finding for source_file in files for finding in scan_file(source_file)]
    _write_findings(sys.stdout, findings)
    return _summarize(findings, scanned=len(files))


if __name__ == "__main__":
    raise SystemExit(main())
