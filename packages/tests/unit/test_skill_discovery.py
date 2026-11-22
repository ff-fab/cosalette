"""Unit tests for repository skill wiring — Copilot, Claude Code and Codex discovery.

Test Techniques Used:
- Specification-based: every `.github/skills/<name>` is linked from `.claude/skills`
  and `.agents/skills`, and its frontmatter meets the Agent Skills limits that Claude
  Code and Codex enforce (name ≤ 64 hyphen-case chars matching the directory,
  description ≤ 1024 chars without angle brackets or newlines once YAML-parsed,
  so folded `>` blocks are fine)
- Error Guessing: host-specific constructs (`$ARGUMENTS`, which Codex does not
  substitute) and drift between the mirrored CLAUDE.md / AGENTS.md skills sections
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / ".github" / "skills"
SKILLS = sorted(p.parent.name for p in SOURCE.glob("*/SKILL.md"))
HOST_DIRS = [".claude/skills", ".agents/skills"]  # Claude Code, Codex


def _frontmatter(skill: str) -> dict[str, Any]:
    text = (SOURCE / skill / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "frontmatter must start on line 1"
    data: Any = yaml.safe_load(text.split("---\n", 2)[1])
    assert isinstance(data, dict)
    return data


def test_skills_exist() -> None:
    """The source directory holds at least the pr-review skill."""
    assert "pr-review" in SKILLS


@pytest.mark.parametrize("host_dir", HOST_DIRS)
def test_host_dir_links_every_skill_and_nothing_else(host_dir: str) -> None:
    """Each host directory has exactly one relative symlink per source skill."""
    # Arrange
    entries = {p.name: p for p in (ROOT / host_dir).iterdir()}

    # Act / Assert
    assert sorted(entries) == SKILLS, "run task skills:sync"
    for name, link in entries.items():
        assert link.is_symlink(), f"{link} must be a symlink, not a copy"
        assert str(link.readlink()) == f"../../.github/skills/{name}"
        assert (link / "SKILL.md").is_file()


@pytest.mark.parametrize("skill", SKILLS)
def test_frontmatter_valid_for_all_hosts(skill: str) -> None:
    """name and description satisfy the strictest host (Codex skips invalid skills)."""
    # Arrange / Act
    meta = _frontmatter(skill)
    name, description = meta.get("name"), meta.get("description")

    # Assert
    assert name == skill
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill) and len(skill) <= 64
    assert isinstance(description, str)
    description = description.strip()
    assert 0 < len(description) <= 1024
    assert "\n" not in description
    assert not set("<>") & set(description)
    assert meta.get("user-invocable", True) is not False, "hides skill from / menu"


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_body_has_no_host_only_placeholders(skill: str) -> None:
    """`$ARGUMENTS` is substituted by Claude Code only; Codex shows it literally."""
    assert "$ARGUMENTS" not in (SOURCE / skill / "SKILL.md").read_text(encoding="utf-8")


def _skills_section(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## Skills .*?(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert match, f"{path.name} lacks a '## Skills' section"
    return match.group(0)


def test_claude_md_and_agents_md_skills_sections_match() -> None:
    """Claude Code reads CLAUDE.md and Codex reads AGENTS.md; keep the copies equal."""
    assert _skills_section(ROOT / "CLAUDE.md") == _skills_section(ROOT / "AGENTS.md")
