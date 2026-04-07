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
- For supersessions: update the old ADR's status
- For amendments: append the amendment block and update the status line

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
