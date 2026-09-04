"""Unit tests for scripts/generate_adr_index.py — ADR summary extraction.

Test Techniques Used:
- Equivalence Partitioning: plain prose vs. emphasised/quoted first sentences
- Boundary Value Analysis: summary length at the truncation threshold
- Error Guessing: a first sentence that opens a multi-sentence emphasised quote
  (the ADR-068 regression) must not leave unbalanced Markdown in the summary
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="session")
def gen_index() -> ModuleType:
    """Load scripts/generate_adr_index.py as a module (once per test session)."""
    script_path = (
        Path(__file__).resolve().parents[4] / "scripts" / "generate_adr_index.py"
    )
    spec = importlib.util.spec_from_file_location("generate_adr_index", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(body: str) -> str:
    return f"# ADR-999: Test\n\n## Context\n\n{body}\n\n## Decision\n\nx\n"


def test_plain_first_sentence_is_returned_verbatim(gen_index: ModuleType) -> None:
    # Arrange
    content = _context("A plain sentence ends here. A second one follows.")

    # Act
    summary = gen_index.extract_summary_from_content(content)

    # Assert
    assert summary == "A plain sentence ends here."


def test_backticked_code_span_is_preserved(gen_index: ModuleType) -> None:
    """A period inside `code.span` is not a sentence boundary; backticks stay."""
    # Arrange
    content = _context("Set `interval=30.0` on the handler. Then publish.")

    # Act
    summary = gen_index.extract_summary_from_content(content)

    # Assert
    assert summary == "Set `interval=30.0` on the handler."


def test_emphasis_and_quotes_are_stripped_to_plain_text(gen_index: ModuleType) -> None:
    """Emphasis (*) and quote (") markers are removed; backticks remain.

    Technique: Error Guessing — the ADR-068 regression, where a first sentence
    opened an emphasised multi-sentence quotation and the boundary fell inside
    it, leaving an unbalanced `*"` in the summary.
    """
    # Arrange
    content = _context(
        '`ai help` ships this verbatim: *"One rule: if you declare it, state '
        'is validated. It holds everywhere."* The device half is true.'
    )

    # Act
    summary = gen_index.extract_summary_from_content(content)

    # Assert — no unbalanced Markdown, complete sentence, backticks kept
    assert "*" not in summary
    assert '"' not in summary
    assert summary.count("`") % 2 == 0
    assert summary.endswith("validated.")
    assert summary.startswith("`ai help` ships this verbatim:")


def test_long_first_sentence_is_truncated_with_ellipsis(gen_index: ModuleType) -> None:
    # Arrange — a single clause longer than the 200-char cap, no early period
    body = "word " * 60 + "and the clause finally closes here."
    content = _context(body)

    # Act
    summary = gen_index.extract_summary_from_content(content)

    # Assert
    assert summary.endswith("...")
    assert len(summary) <= gen_index._MAX_SUMMARY_LEN + 3
