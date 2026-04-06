---
name: orchestrator-templates
description: Style guide templates for phase completion files, plan completion files, and git commit messages used by the orchestrator agent.
user-invokable: false
disable-model-invocation: true
allowed-tools:
  - Read
  - Bash
---

# Orchestrator Templates

Load this skill when writing phase completion files, plan completion files, or composing
git commit messages.

<phase_complete_style_guide> File name: `<epic-name>-<task-name>-complete.md` (use
kebab-case)

```markdown
## Epic {Epic Name} Complete: {Task Name}

{Brief tl;dr of what was accomplished. 1-3 sentences in length.}

**Files created/changed:**

- File 1
- File 2
- File 3 ...

**Functions created/changed:**

- Function 1
- Function 2
- Function 3 ...

**Tests created/changed:**

- Test 1
- Test 2
- Test 3 ...

**Review Status:** {APPROVED / APPROVED with minor recommendations}

**Git Commit Message:** {Git commit message following <git_commit_style_guide>}
```

</phase_complete_style_guide>

<plan_complete_style_guide> File name: `<epic-name>-complete.md` (use kebab-case)

```markdown
## Epic Complete: {Epic Title}

{Summary of the overall accomplishment. 2-4 sentences describing what was built and the
value delivered.}

**Phases Completed:** {N} of {N}

1. ✅ Phase 1: {Phase Title}
2. ✅ Phase 2: {Phase Title}
3. ✅ Phase 3: {Phase Title} ...

**All Files Created/Modified:**

- File 1
- File 2
- File 3 ...

**Key Functions/Classes Added:**

- Function/Class 1
- Function/Class 2
- Function/Class 3 ...

**Test Coverage:**

- Total tests written: {count}
- All tests passing: ✅

**Recommendations for Next Steps:**

- {Optional suggestion 1}
- {Optional suggestion 2} ...
```

</plan_complete_style_guide>

<git_commit_style_guide>

```
fix/feat/chore/test/refactor: Short description of the change (max 50 characters)

- Concise bullet point 1 describing the changes
- Concise bullet point 2 describing the changes
- Concise bullet point 3 describing the changes
...
```

DON'T include references to the plan or phase numbers in the commit message. The git
log/PR will not contain this information. </git_commit_style_guide>
