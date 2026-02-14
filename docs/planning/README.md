# cosalette — Planning Documents

> **cosalette** — An opinionated Python framework for IoT-to-MQTT bridges.

This directory contains all planning, design, and reference material for
building the cosalette framework. It is designed to be **self-contained** —
everything needed to start development is here.

## Reading Order

### 1. Architecture Proposal (start here)

[cosalette-framework-proposal.md](cosalette-framework-proposal.md)

The complete framework design: API sketches, package structure, MQTT topic
conventions, lifecycle, CLI, testing strategy, migration plan. **This is the
primary document.**

### 2. Requirements & Decisions

[answers.md](answers.md)

The user's project inventory (8 IoT/MQTT projects), architecture preferences,
and decisions on all open questions. Provides context for *why* the proposal
looks the way it does.

### 3. Reference Code (from velux2mqtt)

[reference/](reference/)

Production code extracted from the `velux2mqtt` project. These are the
concrete implementations that cosalette will **generalise**. Each file maps
to a framework component — see [reference/README.md](reference/README.md) for
the mapping table.

| File | Lines | Framework component |
| ---- | ----- | ------------------- |
| `mqtt_client.py` | 356 | `cosalette._mqtt` — MQTT client with reconnection |
| `error_publisher.py` | 251 | `cosalette._errors` — structured error publication |
| `log_format.py` | 105 | `cosalette._logging` — JSON/text log formatter |
| `clock.py` | 38 | `cosalette._clock` — monotonic clock |
| `config.py` | 267 | `cosalette._settings` — base settings (extract MQTT + logging) |
| `protocols.py` | 125 | Port protocol patterns (ClockPort → framework, others → project) |
| `main.py` | 238 | `cosalette._app` — replaced by `App` class |
| `handlers.py` | 112 | `cosalette._router` — generalised topic router |

### 4. Legacy Scripts (real-world examples)

[legacy/](legacy/)

Existing scripts from the user's smart home. These show the **before** state —
what cosalette-based projects will replace. Useful for validating that the
framework API can express these patterns cleanly.

| File | Project | Archetype |
| ---- | ------- | --------- |
| `hmc5883.py` | gas2mqtt | Telemetry (sensor polling → MQTT) |
| `wizcontrol.py` | wiz2mqtt | Command & Control (MQTT ↔ device) |

## Key Decisions (Summary)

| Decision | Choice |
| -------- | ------ |
| Framework name | `cosalette` |
| Framework style | Opinionated, inversion of control ("FastAPI for MQTT daemons") |
| CLI library | Typer |
| Adapter resolution | Both class-based and string-based lazy import |
| Health reporting | Structured JSON + simple LWT fallback |
| MQTT topic convention | `{app}/{device}/state`, `/set`, `/availability` |
| Packaging | PyPI (`cosalette`), independent semver |
| Repository layout | Multi-repo (cosalette + each project separate) |
| Configuration | pydantic-settings with configurable `env_prefix` |
| Logging | JSON (containers) + text (development), correlation metadata |
| Testing | `cosalette.testing` module + pytest plugin |
| Python version | ≥ 3.14 |

## Development Phases

1. **Build cosalette core** — new repo, implement framework components
2. **First consumer: gas2mqtt** — simplest project, validates telemetry archetype
3. **Second consumer: wiz2mqtt** — validates command & control archetype
4. **Migrate velux2mqtt** — port to cosalette once API is battle-tested
5. **Remaining projects** — airthings2mqtt, concept2mqtt, wallpanel2mqtt,
   smartmeter2mqtt, vito2mqtt

## Kickstart Prompt

[kickstart-prompt.md](kickstart-prompt.md)

A ready-to-use prompt for the first development session in the fresh cosalette
project. Copy the text below the horizontal rule into your first chat message.

## Directory Structure

```text
docs/planning/
├── README.md                          ← you are here
├── cosalette-framework-proposal.md    ← architecture proposal
├── answers.md                         ← requirements & decisions
├── kickstart-prompt.md                ← first prompt for fresh project
├── reference/                         ← velux2mqtt source (generalisation input)
│   ├── README.md                      ← mapping table & usage guide
│   ├── mqtt_client.py
│   ├── error_publisher.py
│   ├── log_format.py
│   ├── clock.py
│   ├── config.py
│   ├── protocols.py
│   ├── main.py
│   └── handlers.py
└── legacy/                            ← real-world scripts (before state)
    ├── README.md                      ← context for each script
    ├── hmc5883.py
    └── wizcontrol.py
```
