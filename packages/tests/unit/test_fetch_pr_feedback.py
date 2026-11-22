"""Unit tests for .github/skills/pr-review/fetch-pr-feedback.sh — PR data collection.

A fake `gh` on PATH serves fixture JSON per API endpoint, so no network is used.

Test Techniques Used:
- Specification-based: output conforms to feedback-schema.json
- Error Guessing: gh notices on stderr (e.g. "hint: ... gh event listen") must not
  corrupt the JSON on stdout
- Equivalence Partitioning: any failing gh endpoint makes the script exit non-zero
  with gh's error instead of reporting an empty section
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".github" / "skills" / "pr-review"

FAKE_GH = r"""#!/usr/bin/env bash
# Fake gh: `repo view` -> o/r; `api ENDPOINT [--paginate] [--jq EXPR]` -> fixture.
set -euo pipefail
[[ "$1" == repo ]] && { echo o/r; exit 0; }
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
echo "hint: use gh event listen instead of polling" >&2
if [[ "$endpoint" == "${FAIL_ENDPOINT:-}" ]]; then
    echo "gh: HTTP 502 for $endpoint" >&2
    exit 1
fi
jq -r "$expr" "$FIXTURES/${endpoint//[\/?=]/_}.json"
"""

FIXTURES = {
    "repos/o/r/pulls/7": {
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
    },
    "repos/o/r/pulls/7/files": [
        {
            "filename": "a.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "changes": 1,
            "patch": "@@ -1 +1 @@",
        }
    ],
    "repos/o/r/pulls/7/reviews": [
        {
            "id": 1,
            "user": {"login": "bob"},
            "state": "COMMENTED",
            "body": "b",
            "submitted_at": "2026-01-01T00:00:00Z",
        }
    ],
    "repos/o/r/pulls/7/comments": [
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
    ],
    "repos/o/r/issues/7/comments": [],
    "repos/o/r/commits/abc/status": {"state": "success", "statuses": []},
    "repos/o/r/commits/abc/check-runs?per_page=100": {
        "check_runs": [
            {
                "name": "ci",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/o/r/runs/1",
                "output": {"title": None, "summary": None},
            }
        ]
    },
}


def _run(tmp_path: Path, fail_endpoint: str = "") -> subprocess.CompletedProcess[str]:
    bin_dir, fixtures = tmp_path / "bin", tmp_path / "fixtures"
    bin_dir.mkdir()
    fixtures.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    for endpoint, body in FIXTURES.items():
        name = endpoint.replace("/", "_").replace("?", "_").replace("=", "_")
        (fixtures / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FIXTURES": str(fixtures),
        "FAIL_ENDPOINT": fail_endpoint,
    }
    return subprocess.run(  # noqa: S603 — fixed script path, test-controlled args
        ["bash", str(SKILL / "fetch-pr-feedback.sh"), "7"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_stderr_notices_do_not_corrupt_json(tmp_path: Path) -> None:
    """gh hints on stderr stay out of stdout; every section is populated."""
    # Act
    result = _run(tmp_path)

    # Assert
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    schema = json.loads((SKILL / "feedback-schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    assert data["metadata"]["labels"] == ["bug"]
    assert len(data["changed_files"]) == 1
    assert data["reviews"][0]["author"] == "bob"
    assert data["review_comments"][0]["path"] == "a.py"
    assert data["ci_status"]["state"] == "success"


@pytest.mark.parametrize(
    "endpoint",
    [
        "repos/o/r/pulls/7",
        "repos/o/r/pulls/7/files",
        "repos/o/r/pulls/7/reviews",
        "repos/o/r/pulls/7/comments",
        "repos/o/r/issues/7/comments",
        "repos/o/r/commits/abc/check-runs?per_page=100",
    ],
)
def test_failed_gh_call_exits_nonzero_with_error(tmp_path: Path, endpoint: str) -> None:
    """A failing endpoint aborts the run instead of yielding an empty section."""
    # Act
    result = _run(tmp_path, fail_endpoint=endpoint)

    # Assert
    assert result.returncode != 0
    assert f"gh: HTTP 502 for {endpoint}" in result.stderr
    assert result.stdout == ""
