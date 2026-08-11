"""Tests for cosalette._package_cli — package-level CLI.

Test Techniques Used:
    - Specification-based Testing: CLI command contracts and flag behavior
    - State Transition Testing: File creation, update, no-change scenarios
    - Boundary Value Analysis: Canonical vs custom targets, existing vs missing files
    - Error Path Testing: Missing template files, path resolution failures
    - Error Guessing: Symlink safety, exception fallback paths
    - Property-based Assertion: JSONC surgical edit preserves arbitrary comment bytes
    - Negative Testing: _SURGICAL_FAIL sentinel triggers warn-and-skip without file
      mutation
    - Robustness Testing: CRLF frontmatter delimiters; downstream keys survive refresh
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import types
from pathlib import Path
from textwrap import dedent
from typing import Any, cast
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cosalette._package_cli import (
    _get_canonical_relative_path,
    _get_package_assets_dir,
    _is_canonical_default_target,
    _manage_agent_pointer_block,
    _manage_claude_config,
    _manage_kilo_config,
    _manage_kilo_mcp_config,
    _manage_opencode_config,
    _strip_jsonc_comments,
    app,
)
from cosalette._package_cli._json_config import (
    _is_uv_workspace,
    _relocatable_mcp_command,
)

pytestmark = pytest.mark.unit


# =============================================================================
# Package CLI Wiring Tests
# =============================================================================


class TestPackageCliWiring:
    """Smoke tests that verify top-level command groups are mounted on the root app."""

    def test_schema_command_group_reachable(self, runner: CliRunner) -> None:
        """schema subcommand group is registered on the root app entry point.

        Technique: Specification-based — verifies the CLI wiring contract:
        'cosalette schema --help' must exit 0 and list schema subcommands.
        Regression guard.
        """
        result = runner.invoke(app, ["schema", "--help"])
        assert result.exit_code == 0
        assert "check" in result.output or "validate" in result.output


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def runner() -> CliRunner:
    """CLI runner for testing CLI commands."""
    return CliRunner()


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch):
    """Temporary workspace directory that simulates a real repo structure."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Create the standard directory structure
    (tmp_path / ".github" / "instructions").mkdir(parents=True)

    # Create existing AGENTS.md and CLAUDE.md to simulate real repo
    (tmp_path / "AGENTS.md").write_text("# Agent Instructions\n\nExisting content.\n")
    claude_content = "# CLAUDE Instructions\n\nExisting Claude content.\n"
    (tmp_path / "CLAUDE.md").write_text(claude_content)

    return tmp_path


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestHelperFunctions:
    """Test utility functions for path handling and target validation."""

    def test_canonical_relative_path_normal_case(self, temp_workspace: Path) -> None:
        """Returns relative path when target is under current working directory."""
        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        target.touch()

        result = _get_canonical_relative_path(target)

        assert result == ".github/instructions/cosalette.instructions.md"

    def test_canonical_relative_path_absolute_fallback(self) -> None:
        """Falls back to absolute path when target is outside cwd."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            result = _get_canonical_relative_path(tmp_path)
            assert str(tmp_path.resolve()) in result
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_is_canonical_default_target_positive(self, temp_workspace: Path) -> None:
        """Returns True for the canonical default target path."""
        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )

        result = _is_canonical_default_target(target)

        assert result is True

    def test_is_canonical_default_target_negative(self, temp_workspace: Path) -> None:
        """Returns False for custom target paths."""
        custom_target = temp_workspace / "docs" / "my-instructions.md"

        result = _is_canonical_default_target(custom_target)

        assert result is False

    def test_is_canonical_default_target_error_handling(self) -> None:
        """Returns False gracefully when path resolution fails."""
        invalid_path = Path("/nonexistent/deeply/nested/path/file.md")

        result = _is_canonical_default_target(invalid_path)

        assert result is False


# =============================================================================
# Managed Block Tests
# =============================================================================


class TestManagedAgentBlocks:
    """Test managed pointer block creation and update logic."""

    def test_create_agents_md_from_scratch(self, temp_workspace: Path) -> None:
        """Creates new AGENTS.md file with cosalette framework pointer."""
        agents_path = temp_workspace / "AGENTS.md"
        agents_path.unlink()  # Remove the fixture's AGENTS.md to test creation
        canonical_path = ".github/instructions/cosalette.instructions.md"

        result = _manage_agent_pointer_block(agents_path, canonical_path)

        assert result is True
        assert agents_path.exists()
        content = agents_path.read_text()
        assert "<!-- BEGIN COSALETTE AI SUPPORT v:1 -->" in content
        assert "<!-- END COSALETTE AI SUPPORT -->" in content
        assert "## cosalette Framework Support" in content
        assert canonical_path in content

    def test_skip_creating_nonexistent_claude_md(self, temp_workspace: Path) -> None:
        """Does not create CLAUDE.md when it doesn't exist."""
        # Remove the fixture's CLAUDE.md
        (temp_workspace / "CLAUDE.md").unlink()

        claude_path = temp_workspace / "CLAUDE.md"
        canonical_path = ".github/instructions/cosalette.instructions.md"

        result = _manage_agent_pointer_block(claude_path, canonical_path)

        assert result is False
        assert not claude_path.exists()

    def test_update_existing_agents_md_append_block(self, temp_workspace: Path) -> None:
        """Appends pointer block to existing AGENTS.md without prior block."""
        agents_path = temp_workspace / "AGENTS.md"
        canonical_path = ".github/instructions/cosalette.instructions.md"
        original_content = agents_path.read_text()

        result = _manage_agent_pointer_block(agents_path, canonical_path)

        assert result is True
        new_content = agents_path.read_text()
        assert "Existing content." in new_content  # Preserves original
        assert "<!-- BEGIN COSALETTE AI SUPPORT v:1 -->" in new_content
        assert canonical_path in new_content

    def test_update_existing_claude_md_when_present(self, temp_workspace: Path) -> None:
        """Updates existing CLAUDE.md when file already exists."""
        claude_path = temp_workspace / "CLAUDE.md"
        canonical_path = ".github/instructions/cosalette.instructions.md"
        original_content = claude_path.read_text()

        result = _manage_agent_pointer_block(claude_path, canonical_path)

        assert result is True
        new_content = claude_path.read_text()
        assert "Existing Claude content." in new_content  # Preserves original
        assert "<!-- BEGIN COSALETTE AI SUPPORT v:1 -->" in new_content
        assert canonical_path in new_content

    def test_replace_existing_managed_block(self, temp_workspace: Path) -> None:
        """Replaces existing managed block instead of duplicating."""
        agents_path = temp_workspace / "AGENTS.md"
        canonical_path = ".github/instructions/cosalette.instructions.md"

        # Create file with existing managed block
        existing_content = dedent("""\
            # Agent Instructions

            Some existing content.

            <!-- BEGIN COSALETTE AI SUPPORT v:1 -->

            ## cosalette Framework Support

            Old pointer content here.

            <!-- END COSALETTE AI SUPPORT -->

            More content after.
        """)
        agents_path.write_text(existing_content)

        result = _manage_agent_pointer_block(agents_path, canonical_path)

        assert result is True
        new_content = agents_path.read_text()

        # Should have original content preserved
        assert "Some existing content." in new_content
        assert "More content after." in new_content

        # Should have updated block
        assert "Framework guidance is maintained in" in new_content
        assert canonical_path in new_content

        # Should only have one instance of the markers
        assert new_content.count("BEGIN COSALETTE AI SUPPORT") == 1
        assert new_content.count("END COSALETTE AI SUPPORT") == 1

    def test_no_change_when_block_identical(self, temp_workspace: Path) -> None:
        """Returns False when managed block is already correct."""
        agents_path = temp_workspace / "AGENTS.md"
        canonical_path = ".github/instructions/cosalette.instructions.md"

        # First update to establish correct content
        _manage_agent_pointer_block(agents_path, canonical_path)

        # Second update should detect no changes needed
        result = _manage_agent_pointer_block(agents_path, canonical_path)

        assert result is False

    def test_refuses_symlinked_agent_file(self, temp_workspace: Path) -> None:
        """Refuses to write through a symlinked AGENTS.md (CWE-59)."""
        agents_path = temp_workspace / "AGENTS.md"
        agents_path.unlink()

        # Create a symlink pointing outside the workspace
        target_file = temp_workspace / "outside" / "trap.md"
        target_file.parent.mkdir()
        target_file.write_text("original")
        agents_path.symlink_to(target_file)

        canonical_path = ".github/instructions/cosalette.instructions.md"
        result = _manage_agent_pointer_block(agents_path, canonical_path)

        assert result is False
        assert target_file.read_text() == "original"  # Not overwritten


# =============================================================================
# CLI Command Tests
# =============================================================================


class TestAiInitCommand:
    """Test the main ai init CLI command behavior."""

    def _setup_mock_template(self, temp_workspace: Path, mock_assets_dir):
        """Helper to set up mock template file."""
        template_dir = temp_workspace / "mock_assets"
        template_dir.mkdir()
        template_file = template_dir / "cosalette.instructions.md"
        template_content = (
            "# cosalette Framework Instructions\n\nTemplate content for agents."
        )
        template_file.write_text(template_content)
        mock_assets_dir.return_value = template_dir
        return template_file

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_canonical_install_creates_file_and_manages_agents(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Canonical install creates instruction file and manages AGENTS.md."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 0

        # Should install the template
        instructions_file = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        assert instructions_file.exists()
        assert "Template content for agents" in instructions_file.read_text()

        # Should report successful operations
        assert "✅ Installed cosalette instructions" in result.stdout
        assert "✅ Updated AGENTS.md pointer block" in result.stdout
        assert "✅ Updated CLAUDE.md pointer block" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_custom_target_skips_agent_management(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Custom target installs file but skips AGENTS.md/CLAUDE.md management."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        custom_path = temp_workspace / "docs" / "my-instructions.md"
        result = runner.invoke(app, ["ai", "init", "--target", str(custom_path)])

        assert result.exit_code == 0

        # Should install to custom location
        assert custom_path.exists()
        assert "Template content for agents" in custom_path.read_text()

        # Should skip agent management
        assert "✅ Installed cosalette instructions" in result.stdout
        skip_message = (
            "📝 Custom target path — skipping AGENTS.md/CLAUDE.md auto-management"
        )
        assert skip_message in result.stdout
        assert "Updated AGENTS.md pointer block" not in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_force_flag_overwrites_existing_file(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--force flag overwrites existing instruction file."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        # Create existing file with different content
        instructions_file = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        instructions_file.write_text("# Old instructions content")

        result = runner.invoke(app, ["ai", "init", "--force"])

        assert result.exit_code == 0

        # Should overwrite with template content
        assert "Template content for agents" in instructions_file.read_text()
        assert "Old instructions content" not in instructions_file.read_text()
        assert "✅ Refreshed cosalette instructions" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_existing_file_without_force_exits_with_error(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Existing instruction file without --force should exit with error."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        # Pre-create the file
        instructions_file = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        instructions_file.write_text("# Existing content")

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 1
        assert "❌ Instruction file already exists" in result.stdout
        assert "Use --force to overwrite" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_missing_template_file_error(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Missing template file should cause graceful error."""
        # Set up mock but don't create the template file
        template_dir = temp_workspace / "mock_assets"
        template_dir.mkdir()
        mock_assets_dir.return_value = template_dir
        # Deliberately not creating cosalette.instructions.md

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 1
        assert "❌ Template not found" in result.stdout
        assert "Possible packaging issue or bad dev setup" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_claude_exists_but_no_updates_needed(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Should report when CLAUDE.md exists but no updates are needed."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        # Create CLAUDE.md by running the CLI once first
        result1 = runner.invoke(app, ["ai", "init"])
        assert result1.exit_code == 0

        # Now run again to test the "no updates needed" case
        result2 = runner.invoke(app, ["ai", "init", "--force"])

        assert result2.exit_code == 0
        assert "ℹ️  CLAUDE.md exists but no updates needed" in result2.stdout
        assert "✅ Updated CLAUDE.md pointer block" not in result2.stdout

    def _setup_mock_template_fm(self, temp_workspace: Path, mock_assets_dir):
        """Helper to set up a mock template file WITH frontmatter."""
        template_dir = temp_workspace / "mock_assets"
        if not template_dir.exists():
            template_dir.mkdir()
        template_file = template_dir / "cosalette.instructions.md"
        template_content = dedent("""\
            ---
            description: 'cosalette framework development guidance for AI agents'
            applyTo: '**/*.py'
            ---

            # cosalette Framework Instructions

            Template content for agents.
        """)
        template_file.write_text(template_content)
        mock_assets_dir.return_value = template_dir
        return template_file

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_refresh_preserves_downstream_frontmatter_key(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--force refresh preserves unknown downstream frontmatter keys verbatim."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        target.write_text(
            dedent("""\
            ---
            description: 'old description'
            applyTo: '**'
            paths:
              - .github/instructions/cosalette.instructions.md
            ---

            # Old body content
        """)
        )

        result = runner.invoke(app, ["ai", "init", "--force"])

        assert result.exit_code == 0
        content = target.read_text()
        # Downstream key preserved verbatim
        assert "paths:" in content
        assert "  - .github/instructions/cosalette.instructions.md" in content
        # Owned keys replaced with template values
        assert "description: 'cosalette framework development" in content
        assert "applyTo: '**/*.py'" in content
        # Body replaced with template body
        assert "Template content for agents" in content
        assert "Old body content" not in content

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_check_missing_target_exits_1(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--check on missing target exits 1 with a missing-file message."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init", "--check"])

        assert result.exit_code == 1
        assert "❌ Instruction file missing" in result.output

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_check_up_to_date_target_exits_0(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--check on freshly installed target exits 0."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        runner.invoke(app, ["ai", "init"])
        result = runner.invoke(app, ["ai", "init", "--check"])

        assert result.exit_code == 0
        assert "✅ cosalette instructions are up to date" in result.output

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_check_stale_body_exits_1_with_diff(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--check on a target whose body differs exits 1 and emits a unified diff."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        target.write_text(
            dedent("""\
            ---
            description: 'cosalette framework development guidance for AI agents'
            applyTo: '**/*.py'
            ---

            # Old body content
        """)
        )

        result = runner.invoke(app, ["ai", "init", "--check"])

        assert result.exit_code == 1
        assert "❌ cosalette instructions are out of date" in result.output
        assert "@@" in result.output  # unified diff marker

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_check_extra_downstream_frontmatter_key_exits_0(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--check treats extra downstream frontmatter keys as up to date."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        runner.invoke(app, ["ai", "init"])

        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        # Simulate user adding a downstream key alongside the template-owned ones
        content = target.read_text()
        content = content.replace(
            "applyTo: '**/*.py'\n---",
            "applyTo: '**/*.py'\npaths:\n"
            "  - .github/instructions/cosalette.instructions.md\n---",
        )
        target.write_text(content)

        result = runner.invoke(app, ["ai", "init", "--check"])

        assert result.exit_code == 0
        assert "✅ cosalette instructions are up to date" in result.output

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_claude_imports_agents_md_skips_duplicate_block(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """CLAUDE.md with @AGENTS.md import: skip message, no second cosalette block."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)
        (temp_workspace / "CLAUDE.md").write_text("@AGENTS.md\n\nSome content.\n")

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 0
        assert "already imports AGENTS.md" in result.stdout
        claude_text = (temp_workspace / "CLAUDE.md").read_text()
        assert "<!-- BEGIN COSALETTE AI SUPPORT" not in claude_text

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_claude_symlink_to_agents_md_skips_duplicate_block(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """CLAUDE.md symlinked to AGENTS.md: treated as importing; block skipped."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)
        (temp_workspace / "CLAUDE.md").unlink()
        (temp_workspace / "CLAUDE.md").symlink_to("AGENTS.md")

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 0
        assert "already imports AGENTS.md" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_force_with_crlf_frontmatter_preserves_downstream_key(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--force refresh: CRLF frontmatter with a downstream key — key preserved."""
        self._setup_mock_template_fm(temp_workspace, mock_assets_dir)

        target = (
            temp_workspace / ".github" / "instructions" / "cosalette.instructions.md"
        )
        # Frontmatter uses CRLF line endings; includes an extra downstream key
        crlf_content = (
            "---\r\n"
            "description: 'old description'\r\n"
            "applyTo: '**'\r\n"
            "paths:\r\n"
            "  - .github/instructions/cosalette.instructions.md\r\n"
            "---\r\n"
            "\r\n"
            "# Old body content\r\n"
        )
        target.write_bytes(crlf_content.encode())

        result = runner.invoke(app, ["ai", "init", "--force"])

        assert result.exit_code == 0
        content = target.read_text()
        # Downstream key must survive regardless of line-ending normalisation
        assert "paths:" in content
        assert ".github/instructions/cosalette.instructions.md" in content
        # Template-owned keys must be updated
        assert "cosalette framework development" in content
        assert "applyTo: '**/*.py'" in content


class TestOtherCommands:
    """Test other CLI commands and basic functionality."""

    def test_ai_prime_displays_framework_overview(self, runner: CliRunner) -> None:
        """ai prime command shows framework overview and patterns."""
        result = runner.invoke(app, ["ai", "prime"])

        assert result.exit_code == 0
        assert "cosalette" in result.stdout
        assert "AI Agent Bootstrap" in result.stdout
        assert "AGENTS.md" in result.stdout  # Should mention auto-management
        assert "Framework Patterns" in result.stdout

    def test_ai_prime_upgrade_from_shows_whats_new(self, runner: CliRunner) -> None:
        """ai prime --upgrade-from includes What's New section."""
        result = runner.invoke(app, ["ai", "prime", "--upgrade-from=0.2.1"])

        assert result.exit_code == 0
        assert "cosalette" in result.stdout
        assert "AI Agent Bootstrap" in result.stdout  # Regular content
        assert "What's New (since 0.2.1)" in result.stdout  # Upgrade content
        assert "0.3.0" in result.stdout  # Should show newer versions
        assert "on_configure" in result.stdout  # Should show 0.3.0 features

    def test_ai_prime_upgrade_from_latest_version(self, runner: CliRunner) -> None:
        """ai prime --upgrade-from with latest version shows no What's New."""
        result = runner.invoke(app, ["ai", "prime", "--upgrade-from=0.6.2"])

        assert result.exit_code == 0
        assert "cosalette" in result.stdout
        assert "AI Agent Bootstrap" in result.stdout  # Regular content
        assert "What's New" not in result.stdout  # No upgrade content

    def test_ai_prime_upgrade_from_invalid_version(self, runner: CliRunner) -> None:
        """ai prime --upgrade-from with invalid version shows no What's New."""
        result = runner.invoke(app, ["ai", "prime", "--upgrade-from=invalid.version"])

        assert result.exit_code == 0
        assert "cosalette" in result.stdout
        assert "AI Agent Bootstrap" in result.stdout  # Regular content
        assert "What's New" not in result.stdout  # No upgrade content

    def test_ai_prime_without_upgrade_from_no_whats_new(self, runner: CliRunner):
        """ai prime without --upgrade-from does not include What's New."""
        result = runner.invoke(app, ["ai", "prime"])

        assert result.exit_code == 0
        assert "cosalette" in result.stdout
        assert "AI Agent Bootstrap" in result.stdout
        assert "What's New" not in result.stdout  # No upgrade content

    def test_version_flag_shows_version_info(self, runner: CliRunner) -> None:
        """--version flag displays version and exits cleanly."""
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert "cosalette v" in result.stdout

    def test_ai_help_with_valid_topic(self, runner: CliRunner) -> None:
        """ai help command displays topic-specific guidance."""
        result = runner.invoke(app, ["ai", "help", "telemetry"])

        assert result.exit_code == 0
        assert "Telemetry Development Guide" in result.stdout
        assert "@app.telemetry" in result.stdout

    def test_ai_help_with_invalid_topic(self, runner: CliRunner) -> None:
        """ai help command shows error for unknown topics."""
        result = runner.invoke(app, ["ai", "help", "nonexistent_topic"])

        assert result.exit_code == 1
        assert "❌ Unknown topic: nonexistent_topic" in result.stdout
        assert "Available:" in result.stdout

    def test_top_level_aliases_work(
        self, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Top-level init and prime aliases should work."""
        # Test prime alias (stateless, always works)
        result = runner.invoke(app, ["prime"])
        assert result.exit_code == 0
        assert "AI Agent Bootstrap" in result.stdout

        # Test init alias in isolated workspace with missing assets
        with patch(
            "cosalette._package_cli._get_package_assets_dir",
            return_value=temp_workspace / "missing-assets",
        ):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "Template not found" in result.stdout

    def test_ai_help_architecture_topic(self, runner: CliRunner) -> None:
        """ai help architecture displays architectural guidance."""
        result = runner.invoke(app, ["ai", "help", "architecture"])

        assert result.exit_code == 0
        assert "Architecture + Design Patterns Guide" in result.stdout
        assert "Hexagonal Architecture" in result.stdout

    def test_assets_dir_fallback_on_import_error(self) -> None:
        """_get_package_assets_dir falls back to __file__-relative path."""
        with patch.dict("sys.modules", {"cosalette": None}):
            result = _get_package_assets_dir()

        assert "assets" in str(result)
        assert "guidance" in str(result)

    def test_python_module_main_entry_point(self) -> None:
        """python -m cosalette should work and display help."""
        result = subprocess.run(
            [sys.executable, "-m", "cosalette", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "cosalette" in result.stdout
        assert "Usage:" in result.stdout

    def test_ai_mcp_serve_rejects_sse_transport(self, runner: CliRunner) -> None:
        """MCP SSE is intentionally unavailable for local-only security posture."""
        result = runner.invoke(app, ["ai", "mcp", "serve", "--transport", "sse"])

        assert result.exit_code == 1
        assert "only supports stdio transport" in result.stdout
        assert "import local application code" in result.stdout

    def test_ai_mcp_serve_runs_stdio_transport(self, runner: CliRunner) -> None:
        """Default MCP serve path starts the server with stdio transport."""

        class DummyServer:
            def __init__(self) -> None:
                self.transport: str | None = None

            def run(self, *, transport: str) -> None:
                self.transport = transport

        dummy_server = DummyServer()

        with (
            patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}),
            patch("cosalette._mcp.create_server", return_value=dummy_server),
        ):
            result = runner.invoke(app, ["ai", "mcp", "serve"])

        assert result.exit_code == 0
        assert dummy_server.transport == "stdio"


# =============================================================================
# MCP Configuration Tests
# =============================================================================


class TestAsObjectDict:
    """Tests for _as_object_dict — Equivalence Partitioning over isinstance branch."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ({"key": "val"}, {"key": "val"}),
            ({}, {}),
            (None, {}),
            ([], {}),
            ([1, 2, 3], {}),
            ("string", {}),
            (42, {}),
        ],
    )
    def test_returns_dict_or_empty(
        self, value: object, expected: dict[str, object]
    ) -> None:
        from cosalette._package_cli._json_config import _as_object_dict

        assert _as_object_dict(value) == expected


class TestMcpConfigurationManagement:
    """Test MCP server configuration in .vscode/mcp.json.

    ``temp_workspace`` has no pyproject.toml/uv.lock, so ``_is_uv_workspace``
    is False regardless of whether ``uv`` happens to be on the test runner's
    PATH — the relocatable command deterministically falls back to
    ``python3`` in these tests. The uv-preferred branch is covered separately
    in ``TestRelocatableMcpCommand``.
    """

    def test_mcp_available_creates_vscode_mcp_json(self, temp_workspace: Path) -> None:
        """MCP available → creates .vscode/mcp.json with cosalette entry."""
        from cosalette._package_cli import _manage_mcp_config

        # Inject dummy fastmcp module so the in-function import succeeds
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Verify file was created
        mcp_config = temp_workspace / ".vscode" / "mcp.json"
        assert mcp_config.exists()

        # Verify content
        import json

        config = json.loads(mcp_config.read_text())
        assert "servers" in config
        assert "cosalette" in config["servers"]
        server_config = config["servers"]["cosalette"]
        assert server_config["command"] == "python3"
        assert server_config["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]
        assert "env" in server_config

    def test_mcp_available_existing_json_without_cosalette_adds_entry(
        self, temp_workspace: Path
    ) -> None:
        """MCP available → existing mcp.json without cosalette → adds entry."""
        from cosalette._package_cli import _manage_mcp_config

        # Create existing mcp.json with other server
        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        existing_config = {
            "servers": {
                "other-server": {
                    "command": "other-command",
                    "args": ["--help"],
                }
            }
        }
        import json

        mcp_config.write_text(json.dumps(existing_config, indent=2))

        # Mock the fastmcp import to be available
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Verify cosalette was added while preserving existing
        config = json.loads(mcp_config.read_text())
        assert "other-server" in config["servers"]
        assert "cosalette" in config["servers"]
        server_config = config["servers"]["cosalette"]
        assert server_config["command"] == "python3"
        assert server_config["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]

    def test_mcp_available_existing_json_with_cosalette_skips_idempotent(
        self, temp_workspace: Path
    ) -> None:
        """MCP available → existing mcp.json with cosalette → skips (idempotent)."""
        from cosalette._package_cli import _manage_mcp_config

        # Create existing mcp.json with correct cosalette config
        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        existing_config = {
            "servers": {
                "cosalette": {
                    "command": "python3",
                    "args": ["-m", "cosalette", "ai", "mcp", "serve"],
                    "env": {},
                }
            }
        }
        import json

        original_content = json.dumps(existing_config, indent=2) + "\n"
        mcp_config.write_text(original_content)

        # Mock the fastmcp import to be available
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Verify file was not modified (idempotent)
        assert mcp_config.read_text() == original_content

    def test_mcp_not_available_no_vscode_changes(self, temp_workspace: Path) -> None:
        """MCP not available → no .vscode/mcp.json changes."""
        from cosalette._package_cli import _manage_mcp_config

        # Mock the import statement to raise ImportError for fastmcp
        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            _manage_mcp_config()

        # Verify no .vscode directory or mcp.json was created
        vscode_dir = temp_workspace / ".vscode"
        assert not vscode_dir.exists()

    def test_mcp_not_available_preserves_existing_json(
        self, temp_workspace: Path
    ) -> None:
        """MCP not available with existing mcp.json → no changes."""
        from cosalette._package_cli import _manage_mcp_config

        # Create existing mcp.json
        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        existing_config = {
            "servers": {
                "other-server": {
                    "command": "other-command",
                    "args": ["--help"],
                }
            }
        }
        import json

        original_content = json.dumps(existing_config, indent=2)
        mcp_config.write_text(original_content)

        # Mock the import statement to raise ImportError for fastmcp
        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            _manage_mcp_config()

        # Verify existing file was not modified
        assert mcp_config.read_text() == original_content

    def test_mcp_available_handles_malformed_json_gracefully(
        self, temp_workspace: Path
    ) -> None:
        """MCP available with malformed existing json → overwrites config."""
        from cosalette._package_cli import _manage_mcp_config

        # Create malformed mcp.json
        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        mcp_config.write_text("{ invalid json content")

        # Mock the fastmcp import to be available
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Verify file was overwritten with correct config
        import json

        config = json.loads(mcp_config.read_text())
        assert "servers" in config
        assert "cosalette" in config["servers"]
        server_config = config["servers"]["cosalette"]
        assert server_config["command"] == "python3"
        assert server_config["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]

    def test_mcp_available_symlinked_vscode_dir_skips(
        self, temp_workspace: Path
    ) -> None:
        """MCP available → symlinked .vscode/ dir → skips (CWE-59)."""
        from cosalette._package_cli import _manage_mcp_config

        # Create a symlink for .vscode pointing outside workspace
        target_dir = temp_workspace / "outside" / ".vscode"
        target_dir.mkdir(parents=True)
        vscode_link = temp_workspace / ".vscode"
        vscode_link.symlink_to(target_dir)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Should not create mcp.json through symlink
        assert not (target_dir / "mcp.json").exists()

    def test_mcp_available_symlinked_mcp_json_skips(self, temp_workspace: Path) -> None:
        """MCP available → symlinked mcp.json → skips (CWE-59)."""
        from cosalette._package_cli import _manage_mcp_config

        # Create real .vscode dir but symlink mcp.json
        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        trap_file = temp_workspace / "outside" / "trap.json"
        trap_file.parent.mkdir(parents=True)
        trap_file.write_text("{}")
        (vscode_dir / "mcp.json").symlink_to(trap_file)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        # Trap file should not be overwritten
        assert trap_file.read_text() == "{}"

    def test_mcp_available_non_dict_json_overwrites(self, temp_workspace: Path) -> None:
        """MCP available → non-dict JSON in mcp.json → treats as malformed."""
        from cosalette._package_cli import _manage_mcp_config

        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        mcp_config.write_text("[1, 2, 3]")

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        import json

        config = json.loads(mcp_config.read_text())
        assert isinstance(config, dict)
        assert "cosalette" in config["servers"]
        assert config["servers"]["cosalette"]["command"] == "python3"

    @pytest.mark.parametrize(
        "servers_value",
        [None, [], "unexpected", 42],
        ids=["null", "array", "string", "int"],
    )
    def test_mcp_available_malformed_servers_field_overwrites(
        self, temp_workspace: Path, servers_value: object
    ) -> None:
        """Valid root dict but malformed 'servers' → cosalette entry written.

        Error Guessing: _as_object_dict was introduced to handle this exact
        scenario without raising AttributeError on .get().
        """
        import json

        from cosalette._package_cli import _manage_mcp_config

        vscode_dir = temp_workspace / ".vscode"
        vscode_dir.mkdir()
        mcp_config = vscode_dir / "mcp.json"
        mcp_config.write_text(json.dumps({"servers": servers_value, "other": "kept"}))

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_mcp_config()

        config = json.loads(mcp_config.read_text())
        assert isinstance(config["servers"], dict)
        assert "cosalette" in config["servers"]
        assert config["servers"]["cosalette"]["command"] == "python3"
        assert config["other"] == "kept"


# =============================================================================
# Relocatable MCP Command Tests (ADR-052)
# =============================================================================


_WHICH_TARGET = "cosalette._package_cli._json_config.shutil.which"


class TestRelocatableMcpCommand:
    """Test the uv-preferred / python3-fallback relocatable command builder.

    Test Techniques Used:
        - Decision Table Testing: uv-on-PATH × uv-workspace-detected combinations
        - Boundary Value Analysis: pyproject.toml-only vs uv.lock-only detection
    """

    def test_prefers_uv_when_on_path_and_workspace_detected(
        self, tmp_path: Path
    ) -> None:
        """uv on PATH + pyproject.toml present → emits uv run invocation."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

        with patch(_WHICH_TARGET, return_value="/usr/bin/uv"):
            result = _relocatable_mcp_command(tmp_path)

        assert result["command"] == "uv"
        assert result["args"] == [
            "run",
            "--package",
            "cosalette",
            "python",
            "-m",
            "cosalette",
            "ai",
            "mcp",
            "serve",
        ]

    def test_detects_uv_workspace_via_uv_lock(self, tmp_path: Path) -> None:
        """uv on PATH + uv.lock (no pyproject.toml) → still counts as uv workspace."""
        (tmp_path / "uv.lock").write_text("")

        with patch(_WHICH_TARGET, return_value="/usr/bin/uv"):
            result = _relocatable_mcp_command(tmp_path)

        assert result["command"] == "uv"

    def test_falls_back_to_python3_when_uv_not_on_path(self, tmp_path: Path) -> None:
        """pyproject.toml present but uv missing from PATH → python3 fallback."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

        with patch(_WHICH_TARGET, return_value=None):
            result = _relocatable_mcp_command(tmp_path)

        assert result["command"] == "python3"
        assert result["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]

    def test_falls_back_to_python3_when_not_uv_workspace(self, tmp_path: Path) -> None:
        """uv on PATH but no pyproject.toml/uv.lock → python3 fallback.

        Regression guard: uv being globally installed must not be enough on
        its own to select the uv invocation outside a uv-managed workspace.
        """
        with patch(_WHICH_TARGET, return_value="/usr/bin/uv"):
            result = _relocatable_mcp_command(tmp_path)

        assert result["command"] == "python3"

    def test_is_uv_workspace_false_for_empty_dir(self, tmp_path: Path) -> None:
        """_is_uv_workspace returns False when neither marker file exists."""
        assert _is_uv_workspace(tmp_path) is False

    def test_is_uv_workspace_true_for_pyproject_toml(self, tmp_path: Path) -> None:
        """_is_uv_workspace returns True when pyproject.toml exists."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        assert _is_uv_workspace(tmp_path) is True


# =============================================================================
# Claude Code MCP Configuration Tests (ADR-052)
# =============================================================================


class TestClaudeConfigManagement:
    """Test MCP server configuration in root .mcp.json for Claude Code.

    Test Techniques Used:
        - State Transition Testing: create/merge/idempotent/skip scenarios
        - Error Guessing: symlink safety, malformed JSON handling
        - Mirrors TestMcpConfigurationManagement's coverage for the new target.
    """

    def test_creates_mcp_json_when_absent(self, temp_workspace: Path) -> None:
        """MCP available → creates root .mcp.json with mcpServers.cosalette."""
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_claude_config(temp_workspace)

        mcp_config = temp_workspace / ".mcp.json"
        assert mcp_config.exists()

        import json

        config = json.loads(mcp_config.read_text())
        assert "mcpServers" in config
        assert "cosalette" in config["mcpServers"]
        server_config = config["mcpServers"]["cosalette"]
        assert server_config["command"] == "python3"
        assert server_config["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]
        assert "env" in server_config

    def test_merges_into_existing_mcp_json_preserving_other_entries(
        self, temp_workspace: Path
    ) -> None:
        """Existing .mcp.json with another server → cosalette entry is added."""
        import json

        mcp_config = temp_workspace / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {"mcpServers": {"other-server": {"command": "other", "args": []}}},
                indent=2,
            )
        )

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_claude_config(temp_workspace)

        config = json.loads(mcp_config.read_text())
        assert "other-server" in config["mcpServers"]
        assert "cosalette" in config["mcpServers"]

    def test_idempotent_when_already_configured(self, temp_workspace: Path) -> None:
        """Already-correct cosalette entry → file left byte-identical."""
        import json

        mcp_config = temp_workspace / ".mcp.json"
        existing = {
            "mcpServers": {
                "cosalette": {
                    "command": "python3",
                    "args": ["-m", "cosalette", "ai", "mcp", "serve"],
                    "env": {},
                }
            }
        }
        original_content = json.dumps(existing, indent=2) + "\n"
        mcp_config.write_text(original_content)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_claude_config(temp_workspace)

        assert mcp_config.read_text() == original_content

    def test_handles_malformed_json_gracefully(self, temp_workspace: Path) -> None:
        """Malformed existing .mcp.json → overwritten with a fresh valid config."""
        mcp_config = temp_workspace / ".mcp.json"
        mcp_config.write_text("{ invalid json")

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_claude_config(temp_workspace)

        import json

        config = json.loads(mcp_config.read_text())
        assert "cosalette" in config["mcpServers"]

    def test_symlinked_mcp_json_skips(self, temp_workspace: Path) -> None:
        """Symlinked .mcp.json → skipped (CWE-59), trap file untouched."""
        trap_file = temp_workspace / "outside" / "trap.json"
        trap_file.parent.mkdir(parents=True)
        trap_file.write_text("{}")
        (temp_workspace / ".mcp.json").symlink_to(trap_file)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_claude_config(temp_workspace)

        assert trap_file.read_text() == "{}"

    def test_mcp_not_available_no_changes(self, temp_workspace: Path) -> None:
        """fastmcp not installed → no .mcp.json is created."""

        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            _manage_claude_config(temp_workspace)

        assert not (temp_workspace / ".mcp.json").exists()


# =============================================================================
# Kilo MCP Configuration Tests (ADR-052)
# =============================================================================


class TestKiloMcpConfigManagement:
    """Test the mcp.cosalette entry written into kilo.jsonc.

    Test Techniques Used:
        - State Transition Testing: create/merge/idempotent/skip scenarios
        - Error Guessing: symlink safety, malformed JSONC handling
    """

    def test_creates_kilo_jsonc_mcp_entry_when_absent(
        self, temp_workspace: Path
    ) -> None:
        """MCP available → creates kilo.jsonc with mcp.cosalette entry."""
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        kilo_config = temp_workspace / "kilo.jsonc"
        assert kilo_config.exists()

        import json

        config = json.loads(kilo_config.read_text())
        assert "mcp" in config
        cos_entry = config["mcp"]["cosalette"]
        assert cos_entry["type"] == "local"
        assert cos_entry["command"] == "python3"
        assert cos_entry["args"] == ["-m", "cosalette", "ai", "mcp", "serve"]

    def test_merges_mcp_entry_preserving_other_mcp_servers(
        self, temp_workspace: Path
    ) -> None:
        """Existing kilo.jsonc mcp block with another server → cosalette added."""
        import json

        kilo_config = temp_workspace / "kilo.jsonc"
        kilo_config.write_text(
            json.dumps(
                {"mcp": {"other-server": {"type": "local", "command": "other"}}},
                indent=2,
            )
        )

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        config = json.loads(kilo_config.read_text())
        assert "other-server" in config["mcp"]
        assert "cosalette" in config["mcp"]

    def test_idempotent_when_already_configured(self, temp_workspace: Path) -> None:
        """Already-correct cosalette entry → file left byte-identical."""
        import json

        kilo_config = temp_workspace / "kilo.jsonc"
        existing = {
            "mcp": {
                "cosalette": {
                    "type": "local",
                    "command": "python3",
                    "args": ["-m", "cosalette", "ai", "mcp", "serve"],
                }
            }
        }
        original_content = json.dumps(existing, indent=2) + "\n"
        kilo_config.write_text(original_content)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        assert kilo_config.read_text() == original_content

    def test_handles_malformed_jsonc_gracefully(self, temp_workspace: Path) -> None:
        """Malformed existing kilo.jsonc → overwritten with a fresh valid config."""
        kilo_config = temp_workspace / "kilo.jsonc"
        kilo_config.write_text("{ not valid ]")

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        import json

        config = json.loads(kilo_config.read_text())
        assert "cosalette" in config["mcp"]

    def test_symlinked_kilo_jsonc_skips(self, temp_workspace: Path) -> None:
        """Symlinked kilo.jsonc → skipped (CWE-59), trap file untouched."""
        trap = temp_workspace / "outside" / "trap.jsonc"
        trap.parent.mkdir(parents=True)
        trap.write_text("{}")
        (temp_workspace / "kilo.jsonc").symlink_to(trap)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        assert trap.read_text() == "{}"

    def test_mcp_not_available_no_changes(self, temp_workspace: Path) -> None:
        """fastmcp not installed → no kilo.jsonc is created."""

        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            _manage_kilo_mcp_config(temp_workspace)

        assert not (temp_workspace / "kilo.jsonc").exists()

    def test_strips_comments_when_merging_into_existing_jsonc(
        self, temp_workspace: Path
    ) -> None:
        """Existing kilo.jsonc with comments and an instructions array still merges.

        The mcp-entry merge path parses via comment-stripping (unlike the
        surgical instructions editor), so the resulting file is valid JSON
        with the cosalette mcp entry present alongside prior keys.
        """
        kilo_config = temp_workspace / "kilo.jsonc"
        kilo_config.write_text(
            "{\n"
            "  // kilo configuration\n"
            '  "instructions": [".kilo/rules/style.md"]\n'
            "}\n"
        )

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            _manage_kilo_mcp_config(temp_workspace)

        import json

        config = json.loads(kilo_config.read_text())
        assert ".kilo/rules/style.md" in config["instructions"]
        assert "cosalette" in config["mcp"]


class TestOpencodeConfigManagement:
    """Test opencode.json creation and update for opencode.ai support."""

    CANONICAL_PATH = ".github/instructions/cosalette.instructions.md"

    def test_creates_opencode_json_when_absent(self, temp_workspace: Path) -> None:
        """Creates opencode.json with instructions entry when file does not exist."""

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        config_path = temp_workspace / "opencode.json"
        assert config_path.exists()

        import json

        config = json.loads(config_path.read_text())
        assert self.CANONICAL_PATH in config["instructions"]
        assert config.get("$schema") == "https://opencode.ai/config.json"

    def test_adds_entry_to_existing_opencode_json(self, temp_workspace: Path) -> None:
        """Appends cosalette path to existing instructions list without overwriting."""

        import json

        config_path = temp_workspace / "opencode.json"
        config_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "instructions": ["docs/rules.md"],
                },
                indent=2,
            )
        )

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        config = json.loads(config_path.read_text())
        assert "docs/rules.md" in config["instructions"]
        assert self.CANONICAL_PATH in config["instructions"]

    def test_idempotent_when_entry_already_present(self, temp_workspace: Path) -> None:
        """Does not duplicate the entry when already present."""

        import json

        config_path = temp_workspace / "opencode.json"
        initial = json.dumps({"instructions": [self.CANONICAL_PATH]}, indent=2) + "\n"
        config_path.write_text(initial)

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        config = json.loads(config_path.read_text())
        assert config["instructions"].count(self.CANONICAL_PATH) == 1
        # File should not have been rewritten (content is unchanged)
        assert config_path.read_text() == initial

    def test_handles_malformed_json_gracefully(self, temp_workspace: Path) -> None:
        """Skips update when opencode.json contains malformed JSON (fail-closed)."""

        config_path = temp_workspace / "opencode.json"
        original_content = "{ not valid json ]"
        config_path.write_text(original_content)

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        # File must be unchanged — we never clobber configs we cannot parse
        assert config_path.read_text() == original_content

    def test_handles_non_list_instructions_value(self, temp_workspace: Path) -> None:
        """Treats non-list instructions value as empty list and adds entry."""

        import json

        config_path = temp_workspace / "opencode.json"
        config_path.write_text(
            json.dumps({"instructions": "string_not_list"}, indent=2)
        )

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        config = json.loads(config_path.read_text())
        assert self.CANONICAL_PATH in config["instructions"]

    def test_refuses_symlinked_config_file(self, temp_workspace: Path) -> None:
        """Skips update when opencode.json is a symlink (CWE-59)."""

        trap = temp_workspace / "trap.json"
        trap.write_text('{"instructions": []}')
        (temp_workspace / "opencode.json").symlink_to(trap)

        _manage_opencode_config(self.CANONICAL_PATH, temp_workspace)

        import json

        assert json.loads(trap.read_text()) == {"instructions": []}  # Unchanged


class TestKiloConfigManagement:
    """Test kilo.jsonc creation and update for kilo.ai support.

    Test Techniques Used:
        - State Transition Testing: create/append/idempotent/skip scenarios
        - Boundary Value Analysis: empty array, comment-only array, malformed JSON
        - Error Guessing: symlink safety, _SURGICAL_FAIL warn-and-skip path
        - Regression Testing: Bug A (trailing inline comment after last element),
          Bug B (comment-only array)
    """

    CANONICAL_PATH = ".github/instructions/cosalette.instructions.md"

    def test_creates_kilo_jsonc_when_absent(self, temp_workspace: Path) -> None:
        """Creates kilo.jsonc with instructions entry when file does not exist."""

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        config_path = temp_workspace / "kilo.jsonc"
        assert config_path.exists()

        import json

        config = json.loads(config_path.read_text())
        assert self.CANONICAL_PATH in config["instructions"]

    def test_adds_entry_to_existing_kilo_jsonc(self, temp_workspace: Path) -> None:
        """Appends cosalette path to existing instructions list without overwriting."""

        import json

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            json.dumps({"instructions": [".kilo/rules/formatting.md"]}, indent=2)
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        config = json.loads(config_path.read_text())
        assert ".kilo/rules/formatting.md" in config["instructions"]
        assert self.CANONICAL_PATH in config["instructions"]

    def test_parses_jsonc_with_line_comments(self, temp_workspace: Path) -> None:
        """Correctly parses kilo.jsonc files containing // line comments."""

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            "{\n"
            "  // kilo configuration\n"
            '  "instructions": [".kilo/rules/style.md"]\n'
            "}\n"
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        import json

        # File still contains comments after surgical edit — strip before parsing
        raw = config_path.read_text()
        config = json.loads(_strip_jsonc_comments(raw))
        assert self.CANONICAL_PATH in config["instructions"]
        assert ".kilo/rules/style.md" in config["instructions"]

    def test_idempotent_when_entry_already_present(self, temp_workspace: Path) -> None:
        """Does not duplicate the entry when already present."""

        import json

        config_path = temp_workspace / "kilo.jsonc"
        initial = json.dumps({"instructions": [self.CANONICAL_PATH]}, indent=2) + "\n"
        config_path.write_text(initial)

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        config = json.loads(config_path.read_text())
        assert config["instructions"].count(self.CANONICAL_PATH) == 1
        assert config_path.read_text() == initial

    def test_handles_malformed_jsonc_gracefully(self, temp_workspace: Path) -> None:
        """Skips update when kilo.jsonc contains malformed JSONC (fail-closed)."""

        config_path = temp_workspace / "kilo.jsonc"
        original_content = "{ not valid ]"
        config_path.write_text(original_content)

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        # File must be unchanged — we never clobber configs we cannot parse
        assert config_path.read_text() == original_content

    def test_refuses_symlinked_config_file(self, temp_workspace: Path) -> None:
        """Skips update when kilo.jsonc is a symlink (CWE-59)."""

        trap = temp_workspace / "trap.jsonc"
        trap.write_text('{"instructions": []}')
        (temp_workspace / "kilo.jsonc").symlink_to(trap)

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        import json

        assert json.loads(trap.read_text()) == {"instructions": []}  # Unchanged

    def test_surgical_edit_preserves_comments_when_appending(
        self, temp_workspace: Path
    ) -> None:
        """Surgical edit appends to instructions array; JSONC comments are preserved."""

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            "{\n"
            "  // kilo configuration\n"
            '  "instructions": [\n'
            '    ".kilo/rules/style.md"\n'
            "  ]\n"
            "}\n"
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        import json

        raw = config_path.read_text()
        assert "// kilo configuration" in raw  # comment must survive
        config = json.loads(_strip_jsonc_comments(raw))
        assert self.CANONICAL_PATH in config["instructions"]
        assert ".kilo/rules/style.md" in config["instructions"]

    def test_surgical_edit_adds_instructions_key_preserving_comments(
        self, temp_workspace: Path
    ) -> None:
        """No instructions key: inserts key after { while preserving JSONC comments."""

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            '{\n  // general settings\n  "model": "claude-opus"\n}\n'
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        import json

        raw = config_path.read_text()
        assert "// general settings" in raw  # comment must survive
        config = json.loads(_strip_jsonc_comments(raw))
        assert self.CANONICAL_PATH in config["instructions"]

    def test_surgical_idempotent_with_comments_byte_identical(
        self, temp_workspace: Path
    ) -> None:
        """Already contains canonical path: no write; file bytes unchanged."""

        config_path = temp_workspace / "kilo.jsonc"
        initial = (
            "{\n"
            "  // kilo configuration\n"
            f'  "instructions": ["{self.CANONICAL_PATH}"]\n'
            "}\n"
        )
        config_path.write_text(initial)

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        assert config_path.read_text() == initial  # byte-identical

    def test_empty_instructions_array_no_leading_comma(
        self, temp_workspace: Path
    ) -> None:
        """Empty instructions array: canonical path inserted without leading comma."""

        import json

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text('{\n  "instructions": []\n}\n')

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        raw = config_path.read_text()
        # Must parse as valid JSON after stripping — a leading comma would break it
        config = json.loads(_strip_jsonc_comments(raw))
        assert config["instructions"] == [self.CANONICAL_PATH]

    def test_block_comment_preserved_when_appending_to_array(
        self, temp_workspace: Path
    ) -> None:
        """/* */ block comment survives surgical append to instructions array."""

        import json

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            "{\n"
            "  /* kilo block comment */\n"
            '  "instructions": [\n'
            '    ".kilo/rules/style.md"\n'
            "  ]\n"
            "}\n"
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        raw = config_path.read_text()
        # Block comment text must survive verbatim
        assert "/* kilo block comment */" in raw
        config = json.loads(_strip_jsonc_comments(raw))
        assert self.CANONICAL_PATH in config["instructions"]
        assert ".kilo/rules/style.md" in config["instructions"]

    def test_surgical_fail_warns_and_leaves_file_unchanged(
        self, temp_workspace: Path, capsys
    ) -> None:
        """instructions value is a string (not array): scanner returns _SURGICAL_FAIL.

        File must be left unchanged and a warning containing 'manually' is printed.
        """

        import json

        config_path = temp_workspace / "kilo.jsonc"
        original = json.dumps({"instructions": "oops"}, indent=2) + "\n"
        config_path.write_text(original)

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        # File must be byte-identical — no rewrite happened
        assert config_path.read_text() == original
        # Warning must mention manual intervention
        captured = capsys.readouterr()
        assert "manually" in captured.out

    def test_trailing_inline_comment_not_corrupted_on_append(
        self, temp_workspace: Path
    ) -> None:
        """Trailing // comment after the last array element survives surgical append.

        Regression for Bug A: the old backward whitespace-only scan would land inside
        the comment text and splice the comma mid-comment, producing invalid JSONC.
        """
        import json

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text(
            "{\n"
            '  "instructions": [\n'
            '    ".kilo/rules/style.md"  // keep this path\n'
            "  ]\n"
            "}\n"
        )

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        raw = config_path.read_text()
        # Comment text must survive verbatim
        assert "// keep this path" in raw
        # Comma must not appear inside the comment text
        comment_idx = raw.index("// keep this path")
        assert "," not in raw[comment_idx : comment_idx + len("// keep this path")]
        # Result must be valid JSON after comment-stripping
        config = json.loads(_strip_jsonc_comments(raw))
        assert ".kilo/rules/style.md" in config["instructions"]
        assert self.CANONICAL_PATH in config["instructions"]

    def test_comment_only_array_treated_as_empty(self, temp_workspace: Path) -> None:
        """Comment-only instructions array is treated as empty; no leading comma.

        Regression for Bug B: the old raw[start+1:close].strip() check returned
        truthy for whitespace+comment content, routing into _append_into_nonempty_array
        and producing a leading comma before the first real element.
        """
        import json

        config_path = temp_workspace / "kilo.jsonc"
        config_path.write_text('{\n  "instructions": [\n    // placeholder\n  ]\n}\n')

        _manage_kilo_config(self.CANONICAL_PATH, temp_workspace)

        raw = config_path.read_text()
        # Comment must survive
        assert "// placeholder" in raw
        # Valid JSON after stripping — a leading comma would cause parse failure
        config = json.loads(_strip_jsonc_comments(raw))
        # Canonical path is the only real entry
        assert config["instructions"] == [self.CANONICAL_PATH]


class TestStripJsoncComments:
    """Test the JSONC comment stripping helper."""

    def test_strips_line_comments(self) -> None:

        result = _strip_jsonc_comments('{\n  // a comment\n  "key": "value"\n}')
        assert "//" not in result
        assert '"key": "value"' in result

    def test_strips_block_comments(self) -> None:

        result = _strip_jsonc_comments('{ /* block comment */ "key": 1 }')
        assert "/*" not in result
        assert '"key": 1' in result

    def test_preserves_https_urls_in_schema(self) -> None:

        text = '{ "$schema": "https://example.com/schema.json" }'
        result = _strip_jsonc_comments(text)
        assert "https://example.com/schema.json" in result

    def test_preserves_comment_markers_inside_string_values(self) -> None:
        """// and /* */ inside quoted strings must not be stripped."""

        import json

        text = '{"msg": "Use // for line comments and /* */ for blocks"}'
        result = _strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed["msg"] == "Use // for line comments and /* */ for blocks"


class TestAiInitOpencodeKiloIntegration:
    """Integration tests: ai init --opencode / --kilo flags."""

    def _setup_mock_template(self, temp_workspace: Path, mock_assets_dir):
        template_dir = temp_workspace / "mock_assets"
        template_dir.mkdir()
        template_file = template_dir / "cosalette.instructions.md"
        template_file.write_text("# cosalette Framework Instructions\n\nContent.")
        mock_assets_dir.return_value = template_dir
        return template_file

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_without_flags_does_not_create_configs(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """ai init with no flags does not create opencode.json or kilo.jsonc."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 0
        assert not (temp_workspace / "opencode.json").exists()
        assert not (temp_workspace / "kilo.jsonc").exists()
        assert "opencode" not in result.stdout
        assert "kilo" not in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_opencode_flag_creates_opencode_json(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--opencode flag creates opencode.json with the instruction file path."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init", "--opencode"])

        assert result.exit_code == 0

        import json

        opencode_path = temp_workspace / "opencode.json"
        assert opencode_path.exists()
        oc = json.loads(opencode_path.read_text())
        assert ".github/instructions/cosalette.instructions.md" in oc["instructions"]
        assert not (temp_workspace / "kilo.jsonc").exists()
        assert "✅ Configured opencode.json" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_kilo_flag_creates_kilo_jsonc(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--kilo flag creates kilo.jsonc with the instruction file path."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init", "--kilo"])

        assert result.exit_code == 0

        import json

        kilo_path = temp_workspace / "kilo.jsonc"
        assert kilo_path.exists()
        kilo = json.loads(kilo_path.read_text())
        assert ".github/instructions/cosalette.instructions.md" in kilo["instructions"]
        assert not (temp_workspace / "opencode.json").exists()
        assert "✅ Configured kilo.jsonc" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_both_flags_creates_both_configs(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--opencode --kilo creates both config files."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init", "--opencode", "--kilo"])

        assert result.exit_code == 0

        import json

        oc = json.loads((temp_workspace / "opencode.json").read_text())
        assert ".github/instructions/cosalette.instructions.md" in oc["instructions"]
        kilo = json.loads((temp_workspace / "kilo.jsonc").read_text())
        assert ".github/instructions/cosalette.instructions.md" in kilo["instructions"]
        assert "✅ Configured opencode.json" in result.stdout
        assert "✅ Configured kilo.jsonc" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_custom_target_skips_opencode_kilo(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Custom --target skips opencode.json and kilo.jsonc even with flags."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        custom_path = temp_workspace / "docs" / "my-rules.md"
        result = runner.invoke(
            app,
            ["ai", "init", "--target", str(custom_path), "--opencode", "--kilo"],
        )

        assert result.exit_code == 0
        assert not (temp_workspace / "opencode.json").exists()
        assert not (temp_workspace / "kilo.jsonc").exists()

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_opencode_flag_emits_deprecation_warning_and_still_writes(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--opencode shows deprecation notice but still creates opencode.json."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        result = runner.invoke(app, ["ai", "init", "--opencode"])

        assert result.exit_code == 0
        assert "--opencode is deprecated" in result.stdout
        assert "kilo" in result.stdout  # mentions --kilo as the preferred flag
        assert (temp_workspace / "opencode.json").exists()
        import json

        oc = json.loads((temp_workspace / "opencode.json").read_text())
        assert ".github/instructions/cosalette.instructions.md" in oc["instructions"]


class TestAiInitClaudeMcpIntegration:
    """Integration tests: ai init --claude flag and the extended --kilo MCP entry."""

    def _setup_mock_template(self, temp_workspace: Path, mock_assets_dir):
        template_dir = temp_workspace / "mock_assets"
        template_dir.mkdir()
        template_file = template_dir / "cosalette.instructions.md"
        template_file.write_text("# cosalette Framework Instructions\n\nContent.")
        mock_assets_dir.return_value = template_dir
        return template_file

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_without_claude_flag_does_not_create_mcp_json(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """ai init with no --claude flag does not create root .mcp.json."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            result = runner.invoke(app, ["ai", "init"])

        assert result.exit_code == 0
        assert not (temp_workspace / ".mcp.json").exists()

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_claude_flag_creates_mcp_json(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--claude flag creates root .mcp.json with mcpServers.cosalette."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            result = runner.invoke(app, ["ai", "init", "--claude"])

        assert result.exit_code == 0

        import json

        mcp_path = temp_workspace / ".mcp.json"
        assert mcp_path.exists()
        config = json.loads(mcp_path.read_text())
        assert "cosalette" in config["mcpServers"]
        assert "✅ Configured .mcp.json" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_kilo_flag_creates_kilo_mcp_entry(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--kilo flag also writes the mcp.cosalette entry into kilo.jsonc."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            result = runner.invoke(app, ["ai", "init", "--kilo"])

        assert result.exit_code == 0

        import json

        kilo = json.loads((temp_workspace / "kilo.jsonc").read_text())
        assert ".github/instructions/cosalette.instructions.md" in kilo["instructions"]
        assert "cosalette" in kilo["mcp"]
        assert "✅ Configured kilo.jsonc for cosalette MCP server" in result.stdout

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_custom_target_skips_claude_mcp_json(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """Custom --target skips .mcp.json generation even with --claude."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        custom_path = temp_workspace / "docs" / "my-rules.md"
        with patch.dict(sys.modules, {"fastmcp": types.ModuleType("fastmcp")}):
            result = runner.invoke(
                app,
                ["ai", "init", "--target", str(custom_path), "--claude", "--kilo"],
            )

        assert result.exit_code == 0
        assert not (temp_workspace / ".mcp.json").exists()
        assert not (temp_workspace / "kilo.jsonc").exists()

    @patch("cosalette._package_cli._get_package_assets_dir")
    def test_ai_init_claude_flag_without_mcp_installed_no_mcp_json(
        self, mock_assets_dir, runner: CliRunner, temp_workspace: Path
    ) -> None:
        """--claude flag with fastmcp not installed → no .mcp.json is created."""
        self._setup_mock_template(temp_workspace, mock_assets_dir)

        real_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = runner.invoke(app, ["ai", "init", "--claude"])

        assert result.exit_code == 0
        assert not (temp_workspace / ".mcp.json").exists()


class TestManifestCommand:
    def test_manifest_invalid_spec_format(self, runner: CliRunner) -> None:
        """Missing colon in spec produces error exit."""
        result = runner.invoke(app, ["manifest", "myapp"])
        assert result.exit_code == 1
        assert "❌" in result.output

    def test_manifest_valid_app_json_output(self, runner: CliRunner) -> None:
        """Valid app spec returns canonical AsyncAPI JSON output."""
        import json
        import sys
        import types

        import cosalette

        fake_module = cast(Any, types.ModuleType("_test_manifest_app"))
        fake_app = cosalette.App(name="testapp", version="1.0.0")

        @fake_app.telemetry("sensor", interval=60, summary="Test sensor")
        async def sensor() -> dict[str, object]:
            return {"value": 42}

        fake_module.app = fake_app
        sys.modules["_test_manifest_app"] = fake_module
        try:
            result = runner.invoke(app, ["manifest", "_test_manifest_app:app"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["asyncapi"] == "3.0.0"
            assert data["info"]["title"] == "testapp"
            assert "sensorState" in data.get("channels", {})
            ch = data["channels"]["sensorState"]
            assert ch.get("x-cosalette-summary") == "Test sensor"
        finally:
            del sys.modules["_test_manifest_app"]

    def test_manifest_valid_app_table_output(self, runner: CliRunner) -> None:
        """--table flag switches to human-readable output with device names."""
        import sys
        import types

        import cosalette

        fake_module = cast(Any, types.ModuleType("_test_manifest_table_app"))
        fake_app = cosalette.App(name="testapp", version="1.0.0")

        @fake_app.telemetry("sensor", interval=60)
        async def sensor() -> dict[str, object]:
            return {"value": 42}

        fake_module.app = fake_app
        sys.modules["_test_manifest_table_app"] = fake_module
        try:
            result = runner.invoke(
                app, ["manifest", "_test_manifest_table_app:app", "--table"]
            )
            assert result.exit_code == 0
            assert "sensor" in result.output
        finally:
            del sys.modules["_test_manifest_table_app"]

    def test_manifest_non_app_object_produces_error(
        self, runner: CliRunner, monkeypatch
    ) -> None:
        """Spec pointing to a non-App object produces a clear error."""
        import types

        # Create a fake module with a non-App attribute
        fake_module = cast(Any, types.ModuleType("fake_manifest_module"))
        fake_module.not_an_app = object()
        import sys

        sys.modules["fake_manifest_module"] = fake_module
        try:
            result = runner.invoke(app, ["manifest", "fake_manifest_module:not_an_app"])
            assert result.exit_code == 1
            assert "not an App instance" in result.output
        finally:
            del sys.modules["fake_manifest_module"]
