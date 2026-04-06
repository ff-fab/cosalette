---
description: Implementation subagent — executes task-specific work delegated by the orchestrator
argument-hint: Task objective, files to modify, test requirements, and acceptance criteria from the orchestrator
tools: ['edit', 'search', 'read', 'execute/runInTerminal', 'execute/getTerminalOutput', 'execute/createAndRunTask', 'execute/testFailure', 'todo']
model: Claude Sonnet 4 (copilot)
---

Execute the implementation task described by the orchestrator. Follow the orchestrator's
task-specific instructions exactly.

**Quality gate:** Before returning, run `task lint` and `task test:unit`. Report results
in `quality_results`.

**Output contract:** Return results as JSON conforming to
`.github/agents/schemas/implementation-output.schema.json`.
