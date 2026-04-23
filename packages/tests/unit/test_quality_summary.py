from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def test_quality_summary_parses_pytest_counts(tmp_path: Path) -> None:
    if shutil.which("jq") is None:
        return

    project_root = Path(__file__).resolve().parents[3]
    script_path = project_root / "scripts" / "quality-summary.sh"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    task_script = fake_bin / "task"
    task_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case "$1" in\n'
        "  lint)\n"
        "    echo 'lint ok'\n"
        "    ;;\n"
        "  typecheck)\n"
        "    echo 'typecheck ok'\n"
        "    ;;\n"
        "  test:unit)\n"
        "    cat <<'EOF'\n"
        "================== 2 passed, 1 skipped in 0.10s ==================\n"
        "EOF\n"
        "    ;;\n"
        "  *)\n"
        '    echo "unexpected task: $1" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    task_script.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] == "PASS"
    assert payload["tests"]["total"] == 3
    assert payload["tests"]["passed_count"] == 2
    assert payload["tests"]["skipped"] == 1
    assert payload["tests"]["failed_count"] == 0
