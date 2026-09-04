---
name: adr-create
description: Create or amend Architecture Decision Records via schema-conforming JSON. Use when the user says "create an ADR", "document this decision", "amend ADR", "supersede ADR", or any variation of recording an architectural decision.
---

# ADR Creation Skill

Create Architecture Decision Records through a structured JSON → Markdown
pipeline. **Never write ADR Markdown directly.** Produce a JSON document
conforming to the schema, run the renderer, verify the output.

## Schema & Tooling

| Resource | Path |
|----------|------|
| JSON Schema | `.github/agents/schemas/adr-input.schema.json` |
| Renderer | `scripts/render_adr.py` |
| Task | `task adr:create -- <input.json>` |
| ADR directory | `docs/adr/` |

## Step 1 — Determine Operation Type

| Scenario | Type |
|----------|------|
| Brand-new decision | `"new"` |
| Typo fix, clarification, add implementation note | `"amendment"` (scope: `"minor"`) |
| Add sub-decisions, new options, extend matrix | `"amendment"` (scope: `"additive"`) |
| Change decision (not yet implemented / low impact) | `"amendment"` (scope: `"corrective"`) |
| Replace a decision (already implemented / high impact) | `"supersede"` |
| Move an ADR's status (e.g. Proposed → Accepted) | `"status"` |

**Default to supersession** when changing an already-implemented decision.
Corrective amendments are the exception — the agent must articulate why
supersession is not warranted in the `amendment_justification` field.

## Step 2 — Assess Impact (new / supersede only)

| Impact | Meaning | Decision matrix | Min options |
|--------|---------|-----------------|-------------|
| `"low"` | Single-module convention, naming, tooling | Optional | 2 |
| `"moderate"` | Affects multiple modules, adds dependency | **Required** (≥3 criteria) | 2 |
| `"high"` | Architectural pattern, cross-cutting, breaking | **Required** (≥5 criteria) | 3 |

## Step 3 — Assign Tags

Choose 1–6 tags from the common vocabulary. Tags use lowercase kebab-case:

`architecture`, `mqtt`, `configuration`, `logging`, `cli`, `testing`,
`packaging`, `dependencies`, `lifecycle`, `telemetry`, `persistence`,
`signal-filters`, `error-handling`, `health`, `scheduling`, `documentation`,
`security`, `di`, `release`, `devices`, `serialization`, `naming`

Create new tags sparingly — prefer reusing existing ones.

## Step 4 — Produce JSON

Construct the JSON object conforming to `adr-input.schema.json`. Key rules:

- **Exactly one** considered option must have `"chosen": true`
- Decision matrix `scores` keys must **exactly match** option `name` fields
- `decision` should be declarative: *"Use X for Y because Z"*
- `context` should include quantitative data where possible
- `decision_drivers` needs ≥3 entries
- Both `consequences_positive` and `consequences_negative` need ≥1 entry

Write the JSON to a temporary file:

```bash
ADR_INPUT=$(mktemp /tmp/adr-input-XXXXXX.json)
cat > "$ADR_INPUT" << 'EOF'
{
  "type": "new",
  "title": "Example Decision",
  "date": "2026-04-07",
  ...
}
EOF
```

## Step 5 — Run Renderer

```bash
task adr:create -- "$ADR_INPUT"
```

The renderer will:
- Validate the JSON structurally
- Auto-number the ADR (scanning `docs/adr/` for the next number)
- Generate canonical Markdown with frontmatter
- For supersessions: update the old ADR's status and write its pointer paragraph
- For amendments: append the amendment block and update the status line
- For status transitions: flip the status token in the frontmatter and `## Status` line

If validation fails, fix the JSON and re-run.

## Step 6 — Verify Output

Read the generated/modified Markdown file and confirm:

- [ ] Frontmatter contains `status`, `date`, `impact`, `tags`
- [ ] Title matches `# ADR-NNN: Title` format
- [ ] Status line is correct
- [ ] Decision matrix is present (if moderate/high impact)
- [ ] All options are rendered with Advantages/Disadvantages
- [ ] Consequences have both Positive and Negative sections
- [ ] Date stamp at the end

## Step 7 — Clean Up & Stage

```bash
rm "$ADR_INPUT"
git add docs/adr/
```

## Amendment Scope Reference

### Minor

Allowed content: `notes`, `additional_consequences_positive`,
`additional_consequences_negative`.

**Not allowed:** `sub_decisions`, `additional_options`,
`additional_matrix_rows`, `revised_decision`,
`revised_decision_code_example`, `revised_decision_code_language`.

### Additive

Allowed content: everything except `revised_decision`.

Use for: adding naming conventions to an architectural pattern ADR, adding
a newly discovered option, extending the decision matrix with new criteria.

### Corrective

Allowed content: everything including `revised_decision`.

**Requires both** `amendment_rationale` and `amendment_justification`.

The justification must explain why supersession is not warranted. Valid
reasons:
- "Decision not yet implemented — no downstream code depends on it"
- "Impact confined to this ADR and one module — negligible migration cost"
- "Library was never adopted — switching is zero-cost"

Invalid reasons (use supersede instead):
- "The old approach has problems" (without addressing implementation impact)
- "We changed our mind" (without impact analysis)

## Supersession Reference

A `supersede` renders the new ADR and marks the old one in three places, all
written by the renderer:

1. frontmatter `status: Superseded by ADR-NNN`
2. the `## Status` body line, `Superseded by ADR-NNN **Date:** <original date>`
3. the pointer paragraph directly beneath it:

```markdown
**Superseded by:** [ADR-070](ADR-070-maturin-build-backend.md) — the build backend is maturin, not hatchling. The PyPI channel, package name, and src layout recorded here remain valid.
```

The prose after the em dash comes from `supersession_note` — say **what
changed** and **what from the old ADR still holds**, so a reader landing on the
superseded ADR knows which parts they can still trust:

```json
{
  "type": "supersede",
  "supersedes_adr": "ADR-008",
  "supersession_note": "the build backend is maturin, not hatchling. The PyPI channel, package name, and src layout recorded here remain valid."
}
```

Rules and guarantees:

- **`supersession_note` is optional but strongly recommended.** Omitting it
  emits the bare `**Superseded by:** [ADR-NNN](file.md)` pointer — structurally
  complete, just without the rationale. Never hand-write the paragraph instead.
- **The renderer owns the separator.** Write the note without a leading dash;
  a leading `—`/`-` is stripped rather than doubled.
- **The link target is derived** from the new ADR's own filename, so it cannot
  drift from the number the renderer assigned.
- **Idempotent:** re-running a supersession against an already-superseded ADR
  rewrites the status line and pointer paragraph in place — it never stacks a
  second one.

## Status Transition Reference

Use `type: "status"` to move an existing ADR between lifecycle states without
touching its content. The common case is flipping a `Proposed` ADR to `Accepted`
once its implementation lands.

```json
{
  "type": "status",
  "target_adr": "ADR-064",
  "status": "Accepted"
}
```

The renderer updates **both** status locations atomically — the frontmatter
`status:` field and the leading token of the `## Status` body line — so they can
never drift. Any date/amendment tail on the Status line (e.g.
`**Date:** 2026-08-31 | Amended **Date:** 2026-09-01`) is preserved; only the
leading status word changes. `task adr:create` then re-renders the derived
indexes (`docs/adr/index.md`, `adr-index.json`), which read the frontmatter
status.

Rules and guarantees:

- **Vocabulary is closed** to `Proposed` and `Accepted`. `Superseded by ADR-NNN`
  is set programmatically by the `supersede` operation and is **not** a valid
  transition target — the renderer refuses to transition an ADR that is already
  superseded.
- **Idempotent:** transitioning to the status an ADR already holds is a no-op.
- **Refuses unknown ADRs** and invalid target statuses.
