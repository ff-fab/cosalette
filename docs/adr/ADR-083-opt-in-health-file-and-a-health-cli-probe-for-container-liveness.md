---
status: Accepted
date: 2026-10-02
impact: moderate
tags: [cli, health, lifecycle, telemetry]
---

# ADR-083: Opt-in health file and a health CLI probe for container liveness

## Status

Accepted **Date:** 2026-10-02 | Amended **Date:** 2026-10-02 | Amended **Date:** 2026-10-04

## Context

A cosalette app reports its health on MQTT: the retained heartbeat on `{prefix}/status` (ADR-012) and, since ADR-080, a `stale` status for telemetry with no fresh data. A container orchestrator cannot read MQTT, though. Docker `HEALTHCHECK` and a Kubernetes exec probe run a command inside the container and look only at its exit code. In the airthings2mqtt outages behind epic cos-4mv5 (proposal item P-5) the process stayed up for 11-38 h while every entity was stale, and nothing restarted it, because nothing outside MQTT could see the problem.

The app CLI is Typer-based (ADR-005) and already has a `schema` subcommand group; the package-level `cosalette` script has `ai` and `schema`. The heartbeat payload is built locally by `HealthReporter`, so it is available even while the broker is unreachable. Many container images run with a read-only root filesystem, and `/tmp` or `$XDG_RUNTIME_DIR` is not always writable or even set, so any default path would fail or warn on some deployments.

## Decision

Use an opt-in health file, written atomically from the heartbeat payload, plus a `health` CLI subcommand that checks it, for container liveness because it lets an orchestrator see the app's own health view without MQTT and without changing anything for apps that do not opt in. The user decided that the file is off by default.

- **Opt-in.** The app writes the file only when the `COSALETTE_HEALTH_FILE` environment variable names a path. There is no default path and no `App()` parameter: the variable name is fixed (not app-prefixed), so the probe, the app and the container image all agree on it without knowing the app's settings prefix.
- **Content.** The file holds the heartbeat payload (`status`, `uptime_s`, `devices`, and `version` unless `include_version=False`) plus `written_at` (Unix time, wall clock) and `interval` (seconds between writes).
- **When.** The first write happens when the run phase starts, before the first MQTT connection, and then every `heartbeat_interval` seconds (60 s when heartbeats are disabled). Writes do not depend on the broker, so an app that cannot reach MQTT still proves its event loop is alive. The file is removed on a clean shutdown.
- **Atomic.** Each write goes to a temporary file in the same directory and replaces the target with `os.replace`, so a probe never reads a half-written file.
- **Soft-fail.** A failed write (read-only filesystem, missing directory) logs one WARNING; repeats log at DEBUG. The app keeps running.
- **Probe.** `<app> health` and `cosalette health` share one implementation with `--file` (default: `COSALETTE_HEALTH_FILE`), `--max-age` (default: 3 x the file's `interval`) and `--fail-on` (device statuses that fail the check; repeatable; default `stale`). It exits 0 when the file is fresh and no device has a failing status, and 1 otherwise (file missing or unreadable, older than `--max-age`, or a failing status). The reason is printed as one line on stderr.
- **Exit on stale.** `App(exit_after_stale=None)` takes seconds. When a telemetry entity has stayed stale (ADR-080) for that long, the app logs CRITICAL and shuts down cleanly with the new exit code 5 (`EXIT_STALE`), so a plain `restart: unless-stopped` policy recovers it without a probe.

```text
# Dockerfile
ENV COSALETTE_HEALTH_FILE=/run/app/health.json
HEALTHCHECK --interval=60s --start-period=120s CMD airthings2mqtt health || exit 1

# app.py — optional: let the process exit after an hour of staleness
app = App("airthings2mqtt", exit_after_stale=3600.0)
```

## Decision Drivers

- An orchestrator must be able to see a stale app without an MQTT client
- Apps that do not opt in must see no change: no new file, no new warning
- A probe must never read a half-written file or depend on the broker being up
- Read-only and minimal container filesystems are common and must not crash the app
- Docker HEALTHCHECK only distinguishes 0 (healthy) from 1 (unhealthy) and reserves 2

## Considered Options

### Option 1: Opt-in health file and health subcommand (chosen)

Write the heartbeat payload to the path in COSALETTE_HEALTH_FILE and check it with `<app> health` / `cosalette health`.

- *Advantages:* Works with every orchestrator that can run a command; No behaviour change unless the variable is set; Reuses the heartbeat payload and ADR-080 statuses
- *Disadvantages:* Needs a writable path chosen by the deployer; Adds a CLI subcommand and an environment variable to document

### Option 2: Health file on by default

Write to a default path such as `$XDG_RUNTIME_DIR` or `/tmp` unless disabled.

- *Advantages:* Probe works without configuration
- *Disadvantages:* The default path is unset or read-only on many images, so most deployments would see a warning; Writes a file for every app, including ones that never probe it

### Option 3: HTTP health endpoint

Serve the heartbeat on a local HTTP port for an HTTP probe.

- *Advantages:* Native Kubernetes httpGet probe
- *Disadvantages:* Adds a server, a port and a dependency to every app; Docker HEALTHCHECK still needs curl or similar in the image

### Option 4: Exit on stale only

Only add `exit_after_stale` and let the restart policy handle it.

- *Advantages:* No file and no CLI
- *Disadvantages:* No way to observe health without killing the process; Does not cover a wedged event loop that never reaches the freshness check

## Decision Matrix

| Criterion | Opt-in health file and health subcommand | Health file on by default | HTTP health endpoint | Exit on stale only |
| --- | --- | --- | --- | --- |
| Works without MQTT | 5 | 5 | 5 | 3 |
| No change for apps that do not opt in | 5 | 2 | 2 | 5 |
| Read-only filesystem safety | 4 | 2 | 5 | 5 |
| Orchestrator compatibility | 5 | 5 | 3 | 4 |
| Implementation and dependency cost | 4 | 4 | 2 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Docker and Kubernetes can mark a stale or wedged app unhealthy with one command
- A missing broker does not make the file go stale, so the probe reports app liveness rather than broker reachability
- `exit_after_stale` gives deployments without a probe a bounded outage
- The `<app>` callback no longer starts the app when a subcommand such as `health` or `schema` is invoked

### Negative

- Deployers must pick a writable path and set the variable in the image
- The probe exit code does not say why the check failed; the reason is only on stderr
- `exit_after_stale` adds exit code 5, which process supervisors must treat as a failure
- The file's `written_at` uses the wall clock, so a large clock jump can make a fresh file look old or an old one look fresh

## Amendment (2026-10-02) — Minor

### Additional Negative Consequences

- With restart_on_stale (ADR-084) also set, exit_after_stale counts from the same stale transition and can end the process before the in-place restart recovers anything. Recovery needs exit_after_stale > check_interval + restart_time + first_cycle_time, where check_interval = min(heartbeat_interval, 60 s, smallest stale_after), restart_time = restart_cooldown plus reset() or re-entry plus the following health check, and first_cycle_time is the first successful cycle of the recreated telemetry. The docs give the rule of thumb exit_after_stale >= 2 x (60 s + restart_cooldown + longest telemetry interval); there is no runtime check.

## Amendment (2026-10-04) — Additive

**Rationale:** The decision's example made a Docker HEALTHCHECK running `<app> health` look like the default way to watch an app. An early adopter (cosalette-apps, on 0.11.0) measured the cost: on a Raspberry Pi 4 with `cpus: 0.5`, each probe took 11-13 s and the probes used about 100 times the app's idle CPU, because every probe starts a full interpreter and imports the framework (`import cosalette` alone loaded about 430 modules). Plain Docker and Docker Compose never restart an unhealthy container, so on those hosts the probe buys a status flag and nothing else. The app already reports the same health on MQTT (ADR-012, ADR-080), and `exit_after_stale` plus `on_task_failure` (ADR-081) let a restart policy recover it. This amendment records which signals deployments should use by default. The mechanism, the CLI and the API are unchanged.

### Additional Sub-Decision: Recommended default: MQTT signals plus supervised restart

Deployment guidance recommends MQTT as the health signal and a restart policy as the recovery path:

- **App alive:** the retained heartbeat on `{prefix}/status` and the LWT `"offline"` on the same topic (ADR-012). A clean shutdown also publishes `"offline"`.
- **Data fresh:** per-device availability topics and the heartbeat's per-device `stale` status (ADR-080).
- **Recovery:** `restart: unless-stopped` (or `on-failure`) together with `App(exit_after_stale=...)` (exit code 5) and `on_task_failure` (exit code 4 once the restart budget is spent, or on the first failure with `"exit"`). The app exits; the supervisor restarts it.

Monitoring alerts on these MQTT signals, debounced so that the `"offline"` a restart publishes does not page anyone. The Docker restart backoff (100 ms, doubling, capped at 1 minute, reset after 10 s of uptime) bounds a crash loop to about one restart a minute.

### Additional Sub-Decision: Health file probe is opt-in for orchestrators that act on it

The health file and the `health` probe stay as decided above, for orchestrators that act on container health: Kubernetes liveness probes, Docker Swarm services, or an autoheal container next to plain Docker. Default examples no longer contain a `HEALTHCHECK`.

The guidance states the cost on constrained hosts: each probe is a full interpreter start plus the app's or the package CLI's imports. Mitigations: compile bytecode at image build time (`UV_COMPILE_BYTECODE=1` or `python -m compileall`), which matters most on a read-only root filesystem where Python cannot cache `.pyc` files; a long `interval` with `start_period` and `start_interval`; a `timeout` sized for the slowest host. Since cos-ht8o.4, `import cosalette` resolves its public names lazily (PEP 562) and loads none of pydantic, typer, aiomqtt or orjson. That is the prerequisite for a stdlib-only probe (cos-ht8o.5); the existing `health` commands still import the app or the package CLI.

### Additional Positive Consequences

- Deployments on plain Docker get recovery from stale telemetry and dead tasks without paying for a probe they cannot act on
- Monitoring and recovery use signals the app already publishes; no new API or dependency

### Additional Negative Consequences

- A wedged event loop never reaches the freshness check, so exit_after_stale cannot fire; the LWT reports it offline once the broker drops the connection, but nothing restarts it on plain Docker until a loop-stall watchdog exists
- Supervised restarts make `{prefix}/status` flap to "offline" and back; alerting has to tolerate short outages

## Amendment (2026-10-04) — Additive

**Rationale:** ADR-087 adds a native `cosalette-health` binary that reads the same health file as the Python probe. Two implementations need a written contract and a way to evolve it, so this amendment records the file format and its version field.

### Additional Sub-Decision: Versioned health-file contract

The health file is a JSON object. `written_at` (Unix seconds) is required and must be a finite number. `interval` is optional and defaults to 60 seconds; the default maximum age is three intervals. `devices` is optional and maps device names to objects whose `status` is a string. Unknown keys are ignored. `render()` writes `health_file_version: 1`; a file without the field is read as version 1, and a file with an unknown major version is reported unhealthy so a newer writer never fools an older probe. The contract is documented in `docs/reference/health-file.md` and enforced by golden fixtures shared by pytest and `cargo test`.

### Additional Sub-Decision: Probe entry points

`cosalette-health` (ADR-087) is the recommended probe. `cosalette health` and `<app> health` stay and call the same stdlib-only implementation in `cosalette._health._probe`. All of them exit 0 or 1 only.

### Additional Positive Consequences

- Python and native probes are held to one contract by shared fixtures

### Additional Negative Consequences

- Incompatible format changes need a new major `health_file_version` and probe releases that understand it
