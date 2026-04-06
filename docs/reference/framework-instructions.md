---
icon: material/file-document-edit
---

# Framework Instructions for Copilot

cosalette ships a ready-made **GitHub Copilot instruction file** that adopter
projects can include to give AI assistants full framework context — API surface,
patterns, constraints, and migration guidance — without manually curating
prompts.

## What the file contains

The instruction file is a comprehensive reference covering:

- **Public API** — every class, protocol, decorator, and function with signatures
  and usage notes
- **Framework patterns** — lifecycle hooks, device archetypes, adapter protocol,
  signal filters, publish strategies
- **Constraints and rules** — what the framework enforces (naming, scoping,
  configuration validation)
- **Migration patterns** — how to move between cosalette versions

## Download

Grab the latest version directly:

- [cosalette-framework-reference.instructions.md](cosalette-framework-reference.instructions.md)

You can also download it from the
[GitHub repository](https://github.com/ff-fab/cosalette/blob/main/docs/reference/cosalette-framework-reference.instructions.md).

## How to include it

1. **Copy the file** into your project:

    ```bash
    cp cosalette-framework-reference.instructions.md \
       .github/instructions/cosalette.instructions.md
    ```

2. **Verify the frontmatter** contains:

    ```yaml
    ---
    applyTo: "**"
    ---
    ```

    This ensures the instructions apply to all files in the workspace.

3. **Done.** GitHub Copilot automatically discovers files in
   `.github/instructions/` and uses them as context for code suggestions,
   chat, and inline completions.

## Versioning

The instruction file is versioned alongside cosalette. When you upgrade
cosalette, check for an updated version of the file and replace your local
copy to keep AI assistance aligned with the framework version you're using.
