"""Unit tests for .github/skills/pr-review/fetch-pr-feedback.sh — PR data collection.

A fake `gh` on PATH serves fixture pages per API endpoint, so no network is used.

Test Techniques Used:
- Specification-based: output conforms to feedback-schema.json
- Error Guessing: gh notices on stderr (e.g. "hint: ... gh event listen") must not
  corrupt the JSON on stdout; sections larger than one argv string (128 KiB)
- Equivalence Partitioning: any failing gh endpoint makes the script exit non-zero
  with gh's error instead of reporting an empty section; CI aggregate states
- Boundary Value Analysis: one page vs. several pages from `--paginate`
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".github" / "skills" / "pr-review"

# `repo view` -> o/r; `pr view` -> 7; `api ENDPOINT [--paginate] [--jq EXPR]` prints
# each fixture page for ENDPOINT, like gh does when paginating.
FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "hint: use gh event listen instead of polling" >&2
case "$1" in
    repo) echo o/r; exit 0 ;;
    pr) if [[ -n "${FAIL_PR_VIEW:-}" ]]; then
            echo "gh: no pull requests found" >&2; exit 1
        fi
        echo 7; exit 0 ;;
esac
shift  # "api"
endpoint="" expr="."
while (($#)); do
    case "$1" in
        --paginate) ;;
        --jq) expr="$2"; shift ;;
        *) endpoint="$1" ;;
    esac
    shift
done
if [[ "$endpoint" == "${FAIL_ENDPOINT:-}" ]]; then
    echo "gh: HTTP 502 for $endpoint" >&2
    exit 1
fi
jq -c --arg e "$endpoint" '.[$e][]' "$FIXTURES" | jq -r "$expr"
"""

REVIEW = {
    "id": 1,
    "user": {"login": "bob"},
    "state": "COMMENTED",
    "body": "b",
    "submitted_at": "2026-01-01T00:00:00Z",
}
SUCCESS_RUN = {
    "name": "ci",
    "status": "completed",
    "conclusion": "success",
    "html_url": "https://github.com/o/r/runs/1",
    "output": {"title": None, "summary": None},
}
CHECK_RUNS = "repos/o/r/commits/abc/check-runs?per_page=100"

# endpoint -> list of response pages
FIXTURES: dict[str, list[Any]] = {
    "repos/o/r/pulls/7": [
        {
            "number": 7,
            "title": "t",
            "body": None,
            "state": "open",
            "draft": False,
            "mergeable_state": "clean",
            "labels": [{"name": "bug"}],
            "user": {"login": "alice"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/pull/7",
            "head": {"sha": "abc"},
        }
    ],
    "repos/o/r/pulls/7/files": [
        [
            {
                "filename": "a.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
                "patch": "@@ -1 +1 @@",
            }
        ]
    ],
    "repos/o/r/pulls/7/reviews": [[REVIEW], [{**REVIEW, "id": 3}]],
    "repos/o/r/pulls/7/comments": [
        [
            {
                "id": 2,
                "user": {"login": "bob"},
                "path": "a.py",
                "line": 1,
                "side": "RIGHT",
                "diff_hunk": "@@",
                "body": "c",
                "created_at": "2026-01-01T00:00:00Z",
                "in_reply_to_id": None,
            }
        ]
    ],
    "repos/o/r/issues/7/comments": [[]],
    "repos/o/r/commits/abc/status": [{"state": "success", "statuses": []}],
    CHECK_RUNS: [{"check_runs": [SUCCESS_RUN]}],
}


def _run(
    tmp_path: Path,
    *args: str,
    fixtures: dict[str, list[Any]] = FIXTURES,
    **env_overrides: str,
) -> subprocess.CompletedProcess[str]:
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    (tmp_path / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}",
        "FIXTURES": str(tmp_path / "fixtures.json"),
        **env_overrides,
    }
    return subprocess.run(  # noqa: S603 — fixed script path, test-controlled args
        ["bash", str(SKILL / "fetch-pr-feedback.sh"), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_fetch_feedback_stderr_hints_returns_valid_json(tmp_path: Path) -> None:
    """gh hints on stderr stay out of stdout; pages merge; every section is filled."""
    # Act
    result = _run(tmp_path, "7")

    # Assert
    assert result.returncode == 0, result.stderr
    assert "hint:" not in result.stderr
    data = json.loads(result.stdout)
    schema = json.loads((SKILL / "feedback-schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    assert data["metadata"]["labels"] == ["bug"]
    assert len(data["changed_files"]) == 1
    assert [r["id"] for r in data["reviews"]] == [1, 3]  # two pages merged
    assert data["review_comments"][0]["path"] == "a.py"
    assert data["conversation_comments"] == []
    assert data["ci_status"]["state"] == "success"


def test_fetch_feedback_section_over_argv_limit_succeeds(tmp_path: Path) -> None:
    """A section larger than one 128 KiB argv string is still assembled."""
    # Arrange
    fixtures = copy.deepcopy(FIXTURES)
    fixtures["repos/o/r/pulls/7/reviews"] = [[{**REVIEW, "body": "x" * 200_000}]]

    # Act
    result = _run(tmp_path, "7", fixtures=fixtures)

    # Assert
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["reviews"][0]["body"]) == 200_000


def test_fetch_feedback_no_argument_autodetects_pr(tmp_path: Path) -> None:
    """Without a PR number the PR of the current branch is used."""
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["metadata"]["number"] == 7


def test_fetch_feedback_autodetect_fails_shows_gh_error(tmp_path: Path) -> None:
    """A failed `gh pr view` reports gh's own error, not just a generic message."""
    result = _run(tmp_path, FAIL_PR_VIEW="1")

    assert result.returncode == 1
    assert "gh: no pull requests found" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("pr_number", ["abc", "-1", "7; rm -rf /"])
def test_fetch_feedback_invalid_pr_number_exits_1(
    tmp_path: Path, pr_number: str
) -> None:
    """Non-numeric PR numbers are rejected before any API call."""
    result = _run(tmp_path, pr_number)

    assert result.returncode == 1
    assert "Invalid PR number" in result.stderr


@pytest.mark.parametrize("endpoint", list(FIXTURES))
def test_fetch_feedback_endpoint_fails_exits_nonzero(
    tmp_path: Path, endpoint: str
) -> None:
    """A failing endpoint aborts the run instead of yielding an empty section."""
    # Act
    result = _run(tmp_path, "7", FAIL_ENDPOINT=endpoint)

    # Assert
    assert result.returncode != 0
    assert f"gh: HTTP 502 for {endpoint}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("check_runs", "expected"),
    [
        ([{**SUCCESS_RUN, "conclusion": "timed_out"}], "failure"),
        ([{**SUCCESS_RUN, "status": "in_progress", "conclusion": None}], "pending"),
        ([], "unknown"),
    ],
)
def test_fetch_feedback_ci_aggregate_state(
    tmp_path: Path, check_runs: list[dict[str, Any]], expected: str
) -> None:
    """Check-run conclusions map onto the aggregate ci_status.state."""
    # Arrange
    fixtures = copy.deepcopy(FIXTURES)
    fixtures[CHECK_RUNS] = [{"check_runs": check_runs}]

    # Act
    result = _run(tmp_path, "7", fixtures=fixtures)

    # Assert
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ci_status"]["state"] == expected
