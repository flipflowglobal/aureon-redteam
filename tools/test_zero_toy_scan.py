"""Tests for tools/zero_toy_scan.py."""

from __future__ import annotations

import logging
import runpy
import shutil
import subprocess
import textwrap
from pathlib import Path, PurePosixPath

import pytest
import zero_toy_scan as zts

SCANNER = Path(__file__).with_name("zero_toy_scan.py")
# Built from pieces so this file does not trip ZT001 itself.
TAGS = ("TO" + "DO", "FIX" + "ME", "HA" + "CK", "X" + "XX", "TB" + "D")


def scan(path: str, source: str) -> list[tuple[int, str]]:
    display = PurePosixPath(path)
    language = zts.language_for(display)
    assert language is not None
    findings = zts.scan_text(display, language, textwrap.dedent(source))
    return [(finding.line, finding.rule.value) for finding in findings]


def codes(path: str, source: str) -> set[str]:
    return {code for _, code in scan(path, source)}


def lex(source: str, language: zts.Language) -> zts.LexResult:
    return zts.lex_c_family(source, zts.DIALECTS[language])


def comment_texts(result: zts.LexResult) -> list[str]:
    return [span.text for span in result.comments]


def string_texts(result: zts.LexResult) -> list[str]:
    return [span.text for span in result.strings]


def git_executable() -> str:
    git = shutil.which("git")
    assert git is not None, "git is required to test work-tree discovery"
    return git


def run_git(repo: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv inside a temporary repository
        [git_executable(), *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
    )


# ZT001 -------------------------------------------------------------------


@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize(
    ("path", "template"),
    [
        ("src/app.py", "value = 1\n# {tag}: finish\n"),
        ("src/lib.rs", "fn f() {{}}\n// {tag} finish\n"),
        ("src/app.ts", "const a = 1;\n// {tag}\n"),
        ("Cargo.toml", "[package]\n# {tag}\n"),
        ("src/Pool.sol", 'string constant S = "{tag}";\n'),
    ],
)
def test_unfinished_tag_is_reported_everywhere(
    tag: str, path: str, template: str
) -> None:
    line = 1 if path.endswith(".sol") else 2
    assert (line, "ZT001") in scan(path, template.format(tag=tag))


def test_unfinished_tag_needs_whole_upper_case_word() -> None:
    tag = TAGS[0]
    source = f"MY_{tag} = 1\n{tag}S = 2\nlower = '{tag.lower()}'\n"
    assert "ZT001" not in codes("src/app.py", source)


# ZT002 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/app.py", "value = 1\n# ...\n"),
        ("src/app.py", "value = 1\n# ... existing code ...\n"),
        ("src/app.py", "value = 1\n# rest of the code\n"),
        ("src/app.py", "value = 1\n# Remainder of implementation\n"),
        ("src/app.py", "value = 1\n# logic unchanged\n"),
        ("src/app.py", "value = 1\n# omitted for brevity\n"),
        ("src/lib.rs", "fn f() {}\n/* ... */\n"),
        ("src/app.ts", "const a = 1;\n// same as above\n"),
        ("src/app.ts", "const a = 1;\n// \u2026\n"),
        ("deploy.sh", "set -eu\n# . . .\n"),
    ],
)
def test_elided_code_is_reported(path: str, source: str) -> None:
    assert (2, "ZT002") in scan(path, source)


def test_ellipsis_inside_prose_is_not_elision() -> None:
    source = "value = 1\n# numpy slicing uses an ellipsis here\n"
    assert "ZT002" not in codes("src/app.py", source)


# ZT003 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "comment",
    [
        "In a real implementation this would call the RPC",
        "in production, you would cache this",
        "for demo purposes",
        "this is just a simplified model",
        "simplified version of the router",
        "add the real logic",
        "your code here",
        "implementation goes here",
        "value is not yet implemented",
        "to be determined",
        "implement this later",
        "left as an exercise",
        "fixed fee for now",
        "temporary workaround",
        "quick fix",
        "should normally fetch from chain",
        "replace with real quotes",
    ],
)
def test_toy_phrase_in_comment_is_reported(comment: str) -> None:
    assert (2, "ZT003") in scan("src/app.py", f"value = 1\n# {comment}\n")


def test_toy_phrase_in_docstring_reports_its_line() -> None:
    source = '''\
    def quote() -> int:
        """Return a quote.

        In a real system this reads the pool.
        """
        return 1
    '''
    assert (4, "ZT003") in scan("src/app.py", source)


def test_toy_phrase_in_hash_comment_language() -> None:
    assert (2, "ZT003") in scan("config.yaml", "key: 1\nother: 2  # for now\n")


def test_plain_comment_is_clean() -> None:
    source = "value = 1  # Fee is rounded down to protect the pool.\n"
    assert scan("src/app.py", source) == []


# ZT004 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "value = 1\n# mock data\n",
        "value = 1\n# Placeholder\n",
        "value = 1\n# stub implementation.\n",
        'value = 1\nLABEL = "mocked price feed"\n',
        'def f() -> int:\n    """Stubbed results."""\n    return 1\n',
    ],
)
def test_test_double_vocabulary_in_production(source: str) -> None:
    assert (2, "ZT004") in scan("src/feed.py", source)


def test_test_double_vocabulary_allowed_in_tests() -> None:
    assert "ZT004" not in codes("tests/test_feed.py", "value = 1\n# mock data\n")


def test_domain_words_are_not_test_double_vocabulary() -> None:
    source = (
        "value = 1\n"
        "# Reject fake pools and use a dummy sender for eth_call.\n"
        "# Replace the link placeholder address before deployment.\n"
    )
    assert "ZT004" not in codes("src/feed.py", source)


def test_rust_cfg_test_module_is_test_code() -> None:
    source = """\
    #[cfg(test)]
    #[expect(clippy::unwrap_used, reason = "tests")]
    mod tests {
        // mock data
        fn f() -> [u8; 1] { [0] }
    }
    // mock data
    """
    assert [item for item in scan("src/lib.rs", source) if item[1] == "ZT004"] == [
        (7, "ZT004"),
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("#[cfg(test)]\nuse crate::x;\n// mock data\n", [(3, "ZT004")]),
        ("#[cfg(test)]\nconst X: [u8; 2] = [1, 2];\n// mock data\n", [(3, "ZT004")]),
        ("#[cfg(test)]\n)\nfn f() {\n// mock data\n}\n", []),
        ("#[cfg(test)]\nmod t {\n// mock data\n", []),
        ("#![cfg(test)]\n// mock data\n", []),
        ("// mock data\n#[cfg(test)]", [(1, "ZT004")]),
    ],
)
def test_rust_cfg_test_item_extent(
    source: str, expected: list[tuple[int, str]]
) -> None:
    assert [
        item for item in scan("src/lib.rs", source) if item[1] == "ZT004"
    ] == expected


# ZT005 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/app.py", 'value = 1\nraise RuntimeError("Not implemented")\n'),
        ("src/app.py", 'value = 1\nMESSAGE = f"not yet implemented: {value}"\n'),
        ("src/app.ts", 'const a = 1;\nthrow new Error("coming soon");\n'),
        ("src/Pool.sol", "uint a;\nstring constant S = 'implement me';\n"),
        ("data.json", '{\n"text": "Lorem ipsum"}\n'),
    ],
)
def test_placeholder_string_is_reported(path: str, source: str) -> None:
    assert (2, "ZT005") in scan(path, source)


def test_placeholder_text_must_start_the_string() -> None:
    source = 'raise ValueError("opcode not implemented by this fork")\n'
    assert "ZT005" not in codes("src/app.py", source)


# ZT006 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/app.py", "value = 1\nif value:  # pragma: no cover\n    value = 2\n"),
        ("src/app.ts", "const a = 1;\n/* istanbul ignore next */\n"),
        ("src/app.ts", "const a = 1;\n// c8 ignore next\n"),
        ("src/lib.rs", "fn f() {}\n// LCOV_EXCL_LINE\n"),
        ("src/lib.rs", "fn f() {}\n#[coverage(off)]\nfn g() {}\n"),
    ],
)
def test_coverage_exclusion_is_reported(path: str, source: str) -> None:
    assert (2, "ZT006") in scan(path, source)


# ZT010 - ZT012 -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "pass",
        "...",
        '"""Documented only."""',
        "raise NotImplementedError",
        'raise NotImplementedError("x")',
        "return",
        "return None",
    ],
)
@pytest.mark.parametrize("keyword", ["def", "async def"])
def test_placeholder_body_is_reported(body: str, keyword: str) -> None:
    source = f"{keyword} handler() -> None:\n    {body}\n"
    found = scan("src/app.py", source)
    assert (1, "ZT010") in found
    assert "ZT011" not in {code for _, code in found}


def test_docstring_then_placeholder_is_a_placeholder_body() -> None:
    source = 'def handler() -> None:\n    """Doc."""\n    pass\n'
    assert scan("src/app.py", source) == [(1, "ZT010")]


@pytest.mark.parametrize(
    "source",
    [
        "def total(a: int, b: int) -> int:\n    return a + b\n",
        "def first(items: list[int]) -> int:\n    value = items[0]\n    return value\n",
        "def fail() -> None:\n    raise ValueError\n",
        (
            "def again() -> None:\n"
            "    try:\n        run()\n    except OSError:\n        raise\n"
        ),
        "def eq(self: object, other: object) -> bool:\n    return NotImplemented\n",
    ],
)
def test_real_bodies_are_clean(source: str) -> None:
    assert scan("src/app.py", source) == []


def test_abstract_overload_and_protocol_bodies_are_exempt() -> None:
    source = """\
    import abc
    import functools
    import typing
    from abc import ABC, abstractmethod
    from typing import Generic, Protocol, TypeVar, overload

    T = TypeVar("T")


    def make_base() -> type:
        return object


    class Pricing(ABC):
        @abstractmethod
        def price(self) -> int:
            \"\"\"Return the price in base units.\"\"\"

        @abc.abstractmethod
        def size(self) -> int: ...

        @functools.lru_cache(maxsize=1)
        def cached(self) -> int:
            return 1


    class Feed(Protocol):
        def latest(self) -> int: ...


    class Box(typing.Protocol[T]):
        def get(self) -> T: ...


    class Other(Generic[T], make_base()):
        value = 1


    @overload
    def parse(raw: int) -> int: ...
    @overload
    def parse(raw: str) -> str: ...
    def parse(raw: int | str) -> int | str:
        return raw
    """
    assert scan("src/app.py", source) == []


def test_pass_in_abstract_method_is_still_a_placeholder_statement() -> None:
    source = """\
    from abc import ABC, abstractmethod


    class Pricing(ABC):
        @abstractmethod
        def price(self) -> int:
            pass
    """
    assert scan("src/app.py", source) == [(7, "ZT011")]


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("try:\n    run()\nexcept OSError:\n    pass\n", 4),
        ("value = 1\n...\n", 2),
        ("class Marker:\n    pass\n", 2),
        ("def f() -> int:\n    value = 1\n    ...\n    return value\n", 3),
    ],
)
def test_placeholder_statement_is_reported(source: str, line: int) -> None:
    assert (line, "ZT011") in scan("src/app.py", source)


@pytest.mark.parametrize(
    "source",
    [
        (
            "def f(x: int) -> int:\n"
            "    if x:\n        raise NotImplementedError\n    return x\n"
        ),
        "import builtins\n\nERROR = builtins.NotImplementedError\n",
    ],
)
def test_not_implemented_error_is_reported(source: str) -> None:
    assert "ZT012" in codes("src/app.py", source)


def test_python_stub_files_may_use_ellipsis_bodies() -> None:
    assert scan("src/app.pyi", "def f() -> int: ...\n") == []


def test_empty_and_comment_only_python_files_are_clean() -> None:
    assert scan("src/__init__.py", "") == []
    assert scan("src/app.py", 'value = ""\n') == []


# ZT013 -------------------------------------------------------------------


def test_rust_placeholder_macros_are_reported_only_in_code() -> None:
    source = """\
    fn a() -> u8 { todo!() }
    fn b() -> u8 { unimplemented!("x") }
    fn c() -> &'static str { "todo!()" }
    // unimplemented!() is banned
    """
    found = [item for item in scan("src/lib.rs", source) if item[1] == "ZT013"]
    assert found == [(1, "ZT013"), (2, "ZT013")]


# ZT900 / ZT901 -----------------------------------------------------------


def test_python_syntax_error_is_a_finding() -> None:
    assert scan("src/app.py", "value = 1\ndef broken(:\n") == [(2, "ZT900")]


def test_python_null_byte_is_a_finding() -> None:
    assert scan("src/app.py", "value = 1\x00\n") == [(1, "ZT900")]


def test_invalid_escape_warning_does_not_abort_the_scan() -> None:
    assert scan("src/app.py", 'PATTERN = "\\d+"\n') == []


def test_scan_file_reads_utf8_with_bom(tmp_path: Path) -> None:
    file = tmp_path / "app.py"
    file.write_bytes("\ufeffvalue = 1\n".encode())
    source_file = zts.SourceFile(file, PurePosixPath("app.py"), zts.Language.PYTHON)
    assert zts.scan_file(source_file) == []


def test_scan_file_reports_undecodable_file(tmp_path: Path) -> None:
    file = tmp_path / "app.py"
    file.write_bytes(b"value = '\xff'\n")
    source_file = zts.SourceFile(file, PurePosixPath("app.py"), zts.Language.PYTHON)
    [finding] = zts.scan_file(source_file)
    assert (finding.line, finding.rule) == (1, zts.Rule.READ_ERROR)


def test_scan_file_reports_unreadable_path(tmp_path: Path) -> None:
    source_file = zts.SourceFile(tmp_path, PurePosixPath("app.py"), zts.Language.PYTHON)
    [finding] = zts.scan_file(source_file)
    assert finding.rule is zts.Rule.READ_ERROR


# C-family lexer ----------------------------------------------------------


def test_lexer_separates_strings_from_comments() -> None:
    result = lex('const u = "http://x"; // real comment\n', zts.Language.JAVASCRIPT)
    assert comment_texts(result) == ["real comment"]
    assert string_texts(result) == ["http://x"]
    assert result.code_mask.startswith("const u = ")
    assert "http" not in result.code_mask


def test_lexer_handles_template_and_single_quoted_strings() -> None:
    source = "const a = `x // y\n/* z */`;\nconst b = '/* q */';\n"
    result = lex(source, zts.Language.JAVASCRIPT)
    assert comment_texts(result) == []
    assert string_texts(result) == ["x // y\n/* z */", "/* q */"]


def test_lexer_skips_empty_string_literals() -> None:
    result = lex("const e = \"\"; const f = ''; // c\n", zts.Language.JAVASCRIPT)
    assert string_texts(result) == []
    assert comment_texts(result) == ["c"]


def test_lexer_recovers_from_unterminated_string_at_newline() -> None:
    result = lex('const s = "abc\n// after\n', zts.Language.JAVASCRIPT)
    assert comment_texts(result) == ["after"]
    assert string_texts(result) == ["abc"]


def test_lexer_honors_escapes_and_line_continuations() -> None:
    source = 'const s = "a\\"b"; // c\nconst t = "x\\\ny"; // d\n'
    result = lex(source, zts.Language.JAVASCRIPT)
    assert comment_texts(result) == ["c", "d"]
    assert string_texts(result) == ['a\\"b', "x\\\ny"]


def test_lexer_unterminated_string_at_end_of_file() -> None:
    result = lex('const s = "abc\\', zts.Language.JAVASCRIPT)
    assert string_texts(result) == ["abc\\"]


def test_block_comment_lines_and_markers() -> None:
    source = "/**\n * Title\n *\n * @param x amount\n */\nconst a = 1;\n"
    result = lex(source, zts.Language.JAVASCRIPT)
    assert [(span.line, span.text) for span in result.comments] == [
        (2, "Title"),
        (4, "@param x amount"),
    ]
    assert result.code_mask.count("\n") == source.count("\n")


def test_line_comment_at_end_of_file_and_empty_comments() -> None:
    result = lex("let a = 1; //! inner doc", zts.Language.RUST)
    assert comment_texts(result) == ["inner doc"]
    assert comment_texts(lex("//\n/**/\n", zts.Language.JAVASCRIPT)) == []


def test_unterminated_block_comment() -> None:
    assert comment_texts(lex("/* open", zts.Language.SOLIDITY)) == ["open"]


def test_rust_block_comments_nest_and_javascript_comments_do_not() -> None:
    rust = lex("/* a /* b */ c */ fn x() {}", zts.Language.RUST)
    assert comment_texts(rust) == ["a /* b */ c"]
    assert "fn x() {}" in rust.code_mask
    javascript = lex("/* a /* b */ c */", zts.Language.JAVASCRIPT)
    assert "c */" in javascript.code_mask


def test_rust_raw_strings() -> None:
    source = 'let s = r#"a "q" // no"#; // yes\nlet b = br"x"; let c = cr##"y"##;\n'
    result = lex(source, zts.Language.RUST)
    assert comment_texts(result) == ["yes"]
    assert string_texts(result) == ['a "q" // no', "x", "y"]


def test_rust_raw_prefix_needs_identifier_start() -> None:
    result = lex('let a = br; let b = ar"x"; let c = b"z";\n', zts.Language.RUST)
    assert string_texts(result) == ["x", "z"]


def test_rust_unterminated_raw_string() -> None:
    assert string_texts(lex('let s = r#"abc', zts.Language.RUST)) == ["abc"]


def test_rust_multiline_string_is_not_a_comment() -> None:
    result = lex('let s = "one\ntwo // no";\n', zts.Language.RUST)
    assert comment_texts(result) == []


def test_rust_chars_and_lifetimes() -> None:
    source = (
        "let q = '\"'; let e = '\\''; let b = '\\\\'; let u = '\\u{1F600}';\n"
        "fn f<'a>(x: &'a str) -> &'a str { x } // c\n"
    )
    result = lex(source, zts.Language.RUST)
    assert comment_texts(result) == ["c"]
    assert string_texts(result) == []


def test_rust_unterminated_escaped_char() -> None:
    result = lex("let a = '\\", zts.Language.RUST)
    assert result.code_mask == "let a = '\\"


def test_solidity_and_json_comments() -> None:
    solidity = lex(
        "/// @notice Swap.\nstring s = 'a//b'; // c\n", zts.Language.SOLIDITY
    )
    assert comment_texts(solidity) == ["@notice Swap.", "c"]
    json = lex('{"a": "//b"} // c\n', zts.Language.JSON)
    assert comment_texts(json) == ["c"]
    assert string_texts(json) == ["a", "//b"]


# Hash comments -----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('echo "a # b" # real\n', ["real"]),
        ("echo $# ${#items[@]}\n", []),
        ("name='it''s' # c\n", ["c"]),
        ('x="a\\"#b" # c\n', ["c"]),
        ("#!/bin/sh\n# full line\n", ["/bin/sh", "full line"]),
        ("url: http://x#frag\n", []),
        ("#\n", []),
        ("name='a\\b' # c\n", ["c"]),
    ],
)
def test_hash_comments(source: str, expected: list[str]) -> None:
    assert [span.text for span in zts.hash_comments(source)] == expected


# Paths and languages -----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/b.py", zts.Language.PYTHON),
        ("a/B.PY", zts.Language.PYTHON),
        ("a/b.pyi", zts.Language.PYTHON_STUB),
        ("a/b.rs", zts.Language.RUST),
        ("a/b.sol", zts.Language.SOLIDITY),
        ("a/b.tsx", zts.Language.JAVASCRIPT),
        ("tsconfig.json", zts.Language.JSON),
        ("ci.yml", zts.Language.HASH_COMMENT),
        ("Dockerfile", zts.Language.HASH_COMMENT),
        ("Makefile", zts.Language.HASH_COMMENT),
        ("package-lock.json", None),
        ("pnpm-lock.yaml", None),
        ("vendor/app.min.js", None),
        ("README.md", None),
        ("Cargo.lock", None),
    ],
)
def test_language_for(path: str, expected: zts.Language | None) -> None:
    assert zts.language_for(PurePosixPath(path)) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_feed.py", True),
        ("pkg/tests/helpers.py", True),
        ("test/Pool.t.sol", True),
        ("web/__tests__/a.ts", True),
        ("benches/bench.rs", True),
        ("src/test_utils.py", True),
        ("src/feed_test.py", True),
        ("conftest.py", True),
        ("src/Pool.t.sol", True),
        ("src/math/tests.rs", True),
        ("web/app.test.ts", True),
        ("web/app.spec.tsx", True),
        ("src/feed.py", False),
        ("script/Deploy.s.sol", False),
        ("src/contest.py", False),
    ],
)
def test_is_test_path(path: str, *, expected: bool) -> None:
    assert zts.is_test_path(PurePosixPath(path)) is expected


# Rendering ---------------------------------------------------------------


def test_finding_render_and_excerpt_truncation() -> None:
    comment = "for now " + "x" * 200
    [finding] = zts.scan_text(
        PurePosixPath("src/a.py"), zts.Language.PYTHON, f"# {comment}\n"
    )
    rendered = finding.render()
    assert rendered.startswith(
        "src/a.py:1: ZT003 toy or hedging phrase: implement it or remove it: "
    )
    assert finding.detail.endswith("...")
    assert len(finding.detail) == 120


# Discovery ---------------------------------------------------------------


def test_discover_explicit_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n")
    (tmp_path / "src" / "notes.md").write_text("text\n")
    (tmp_path / "src" / "node_modules").mkdir()
    (tmp_path / "src" / "node_modules" / "dep.js").write_text("var a;\n")
    (tmp_path / "src" / "vendor").mkdir()
    (tmp_path / "src" / "vendor" / "lib.js").write_text("var b;\n")
    (tmp_path / "src" / "link.py").symlink_to(tmp_path / "src" / "app.py")
    (tmp_path / "solo.rs").write_text("fn f() {}\n")
    files = zts.discover(["src", "solo.rs"], ["src/vendor/*"])
    assert [(item.display.as_posix(), item.language) for item in files] == [
        ("solo.rs", zts.Language.RUST),
        ("src/app.py", zts.Language.PYTHON),
    ]


def test_discover_missing_path_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(zts.ScanError, match=r"path does not exist: nope\.py"):
        zts.discover(["nope.py"], [])


def test_discover_git_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tracked.py").write_text("value = 1\n")
    (tmp_path / "pkg" / "deleted.py").write_text("value = 2\n")
    run_git(tmp_path, "add", ".gitignore", "pkg/tracked.py", "pkg/deleted.py")
    (tmp_path / "pkg" / "deleted.py").unlink()
    (tmp_path / "pkg" / "new.py").write_text("value = 3\n")
    (tmp_path / "ignored.py").write_text("value = 4\n")
    monkeypatch.chdir(tmp_path / "pkg")
    files = zts.discover([], [])
    assert [item.display.as_posix() for item in files] == [
        "pkg/new.py",
        "pkg/tracked.py",
    ]


def test_discover_outside_git_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    with pytest.raises(zts.ScanError, match="git rev-parse --show-toplevel failed"):
        zts.discover([], [])


def test_discover_without_git_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(zts.ScanError, match="git is not installed"):
        zts.discover([], [])


# Command line ------------------------------------------------------------


def test_main_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("value = 1\n")
    caplog.set_level(logging.INFO, logger="zero_toy_scan")
    assert zts.main(["app.py"]) == zts.EXIT_CLEAN
    assert capsys.readouterr().out == ""
    assert "clean: 1 file(s) scanned" in caplog.text


def test_main_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def f() -> None:\n    pass\n")
    (tmp_path / "skip.py").write_text("def g() -> None:\n    pass\n")
    assert zts.main(["app.py", "skip.py", "--exclude", "skip.py"]) == zts.EXIT_FINDINGS
    assert capsys.readouterr().out == (
        "app.py:1: ZT010 function body is only a placeholder: def f\n"
    )
    assert "1 finding(s) in 1 of 1 file(s)" in caplog.text


def test_main_scan_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert zts.main(["missing.py"]) == zts.EXIT_ERROR
    assert "scan could not run: path does not exist: missing.py" in caplog.text


def test_module_runs_as_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("value = 1\n")
    monkeypatch.setattr("sys.argv", ["zero_toy_scan.py", "app.py"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(SCANNER), run_name="__main__")
    assert raised.value.code == zts.EXIT_CLEAN
