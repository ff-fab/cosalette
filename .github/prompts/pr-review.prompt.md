---
agent: agent
description: 'Evaluate PR findings, CI results, and reviewer comments. Suggest fixes and improvements while teaching design patterns, SOLID principles, and language idioms relevant to the changes.'
---

# PR Review & Learning

Analyze the current pull request and provide actionable feedback with educational
context.

## Context Gathering

1. Fetch the active PR details (title, description, changed files, review comments, CI
   status checks) using the available PR tools.
2. Read the changed files to understand the diff.
3. Check CI status — identify any failures, flaky tests, or coverage regressions.

## Analysis Steps

### 1. CI & Status Checks

- Identify failing checks and their root cause
- Flag coverage regressions with specific modules affected
- Note any flaky test patterns

### 2. Review Comments

Triage reviewer comments into categories:

- **Blocking** — must be resolved before merge
- **Suggestion** — improvement ideas, optional
- **Question** — needs a response/clarification

For each comment, propose a concrete fix or response.

### 3. Code Quality Review

Review the diff for:

- **Correctness** — logic errors, edge cases, error handling
- **Style** — consistency with project conventions (see `.github/instructions/`)
- **Performance** — unnecessary allocations, N+1 patterns, blocking I/O
- **Security** — input validation, secrets exposure, injection risks
- **Testability** — missing test coverage, hard-to-test patterns

### 4. Architecture & Design Teaching

For each significant finding, provide educational context following the teaching
structure from user-interaction-style.instructions.md:

1. **What it does** — brief factual description of the pattern or issue
2. **Why this approach** — the reasoning behind the recommendation
3. **Ecosystem context** — reference PEPs, RFCs, official docs by number
4. **Principle connections** — link to SOLID, DRY, GoF design patterns:
   - Name the pattern: Strategy, Factory, Observer, Adapter, etc.
   - Explain which SOLID principle applies (SRP, OCP, LSP, ISP, DIP)
   - Note if DRY/WET trade-offs are relevant
5. **Gotcha** — end with a common pitfall or "watch out for this"

### 5. Language Idiom Spotting

Actively identify opportunities to use language idioms, even in correct code:

- **Python:** walrus operator, structural pattern matching, `itertools`, protocols vs
  ABCs, PEP references
- **TypeScript:** discriminated unions, `satisfies`, `const` assertions, branded types
- Explain _when_ the idiom helps and _when_ it hurts readability

## Output Format

Structure the response as:

```markdown
## PR Summary

Brief description of what the PR does.

## CI Status

- ✅ / ❌ status per check, with failure details

## Review Comments

### Blocking

- [file:line] Comment summary → Suggested fix + rationale

### Suggestions

- [file:line] Comment summary → Recommendation

## Code Quality Findings

### [Category: Correctness/Style/Performance/Security/Testability]

- [file:line] Finding → Fix + teaching moment

## Learning Moments

### [Pattern/Principle Name]

What → Why → Ecosystem → Principle → Gotcha

## Recommended Actions

1. Prioritized list of changes to make
2. Which can be batched vs need sequential work
```

## Interaction Notes

- Be specific: reference exact files and line numbers
- Propose concrete code changes, not vague suggestions
- Teaching should feel natural, not lecturing — weave it into the fix rationale
- If the code is already good, say so and explain _why_ it's good (reinforce learning)
- Acknowledge trade-offs when recommending changes
