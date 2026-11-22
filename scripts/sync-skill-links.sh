#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
source_dir="$repo_root/.github/skills"
# Codex reads .agents/skills; Claude Code reads .claude/skills.
target_dirs=("$repo_root/.agents/skills" "$repo_root/.claude/skills")

if [[ ! -d "$source_dir" ]]; then
  echo "Copilot skills directory not found: $source_dir" >&2
  exit 1
fi

for target_dir in "${target_dirs[@]}"; do
  mkdir -p "$target_dir"

  # Prune links left behind by removed or renamed skills.
  for link in "$target_dir"/*; do
    if [[ -L "$link" && ! -e "$link" && "$(readlink "$link")" == ../../.github/skills/* ]]; then
      rm "$link"
    fi
  done

  for skill_dir in "$source_dir"/*/; do
    [[ -f "$skill_dir/SKILL.md" ]] || continue
    skill_name="${skill_dir%/}"
    skill_name="${skill_name##*/}"
    link="$target_dir/$skill_name"
    expected_target="../../.github/skills/$skill_name"

    if [[ -L "$link" ]]; then
      current_target="$(readlink "$link")"
      if [[ "$current_target" == "$expected_target" ]]; then
        continue
      fi
      echo "Refusing to replace unexpected symlink: $link -> $current_target" >&2
      exit 1
    fi
    if [[ -e "$link" ]]; then
      echo "Refusing to replace existing path: $link" >&2
      exit 1
    fi

    ln -s "$expected_target" "$link"
  done
done

echo "Skills linked from .github/skills into .agents/skills and .claude/skills."
