# GitHub Copilot Instructions

## Project Overview

**cosalette** - a Python project.

## Workflow

- **Branching:** GitHub Flow — branch from `main`, open PR, squash-merge.
- **Commits:** Conventional Commits required (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
- **Releases:** Automated via Release Please (SemVer tags).
- **Never push directly to `main`.**

## GitHub Operations

- Prefer **`gh` CLI** and **`git` CLI** for pull requests, reviews, comments, and issue operations.
- Do not depend on GitKraken MCP authentication for this repository.
- When multiple automation paths exist, choose `gh` commands first.

## Architecture Decision Records

All major decisions are documented in `docs/adr/`. **Follow these decisions.**

Create new ADRs for any major changes or decisions.
