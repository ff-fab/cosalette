# Kickstart Prompt — cosalette Framework Development

I'm starting development of **cosalette** — an opinionated Python framework for
building IoT-to-MQTT bridge applications. Think "FastAPI for MQTT daemons."

All planning, design decisions, reference code, and legacy examples are in
`docs/planning/`. Read them in this order:

1. `docs/planning/README.md` — overview, reading order, key decisions summary
2. `docs/planning/cosalette-framework-proposal.md` — **primary document**: full
   architecture proposal with API sketches, package structure, MQTT topics,
   lifecycle, CLI, testing strategy, and phased migration plan
3. `docs/planning/answers.md` — project inventory (8 IoT/MQTT projects), user
   preferences, and all resolved design questions
4. `docs/planning/reference/README.md` — mapping table showing which velux2mqtt
   file becomes which cosalette component
5. `docs/planning/reference/*.py` — 8 production Python files from velux2mqtt
   that cosalette will **generalise** (MQTT client, error publisher, JSON
   formatter, clock, config, protocols, composition root, command handler)
6. `docs/planning/legacy/README.md` + `legacy/*.py` — 2 real-world scripts
   (gas counter, smart lights) showing legacy code that cosalette-based
   projects will replace


Key technical constraints:
- Dependencies: `aiomqtt>=2.5.0`, `pydantic>=2.12.5`, `pydantic-settings>=2.12.0`, `typer>=0.12`
- Test dependencies: `pytest>=9.0`, `pytest-asyncio>=1.3`, `pytest-cov>=7.0`
- The framework API must support both archetypes from the proposal:
  - **Command & Control** (`@app.device` with `@ctx.on_command`)
  - **Telemetry** (`@app.telemetry` with interval-based polling)
- `App.run()` owns the event loop, signal handlers, logging, CLI, MQTT lifecycle
- `DeviceContext` is the per-device injection point (publish, sleep, commands)
- Reference files in `docs/planning/reference/` are the implementation input —
  generalise them, don't copy them verbatim

The goal is a working `cosalette` package that can be installed from PyPI (or
git) and used to build the gas2mqtt example from the proposal (§8) without any
framework code in the project itself.

After reading all planning docs, please:

1. **Create beads tasks** for Phase 1 (build cosalette core). Break it into
   meaningful, ordered work items with dependencies. The proposal §14 lists
   10 steps — evaluate whether that granularity is right or needs adjustment.

2. **Start implementation** with the first ready task. The project should use:
   - Python ≥ 3.14, `uv` package manager, `hatchling` build backend
   - `pyproject.toml` as single config file (ruff, mypy, pytest, coverage)
   - `src/cosalette/` layout with private modules (`_app.py`, `_mqtt.py`, etc.)
   - `py.typed` marker (PEP 561)
   - Comprehensive docstrings (NumPy/Google style) on all public API
   - Tests alongside implementation — every module gets tests before the next
     module starts

3. **Follow the workflow** in `.github/instructions/workflow.instructions.md`
   (Git flow, beads tracking, pre-PR quality gates, showboat demos).
