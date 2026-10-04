---
icon: material/file-check-outline
---

# Health File

When the `COSALETTE_HEALTH_FILE` environment variable names a path, the app
writes its heartbeat to that file and the health probes check it
([ADR-083](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md)).
This page is the contract between the writer and the probes. The native
`cosalette-health` binary and the Python probes implement the same rules and
share one set of test fixtures
([ADR-087](../adr/ADR-087-native-cosalette-health-probe-binary-shipped-in-platform-wheels.md)).

## Format

The file is one JSON object, written atomically:

```json
{
  "health_file_version": 1,
  "written_at": 1791100800.25,
  "interval": 60.0,
  "status": "online",
  "uptime_s": 3600.0,
  "devices": {
    "sensor": {"status": "ok"},
    "relay": {"status": "stale"}
  }
}
```

| Key | Required | Meaning |
|-----|----------|---------|
| `health_file_version` | no | Contract major version, an integer. A missing key means `1`. |
| `written_at` | yes | Unix time of the write, in seconds. Must be a finite number. |
| `interval` | no | Seconds between writes. A missing, non-numeric or non-positive value means `60`. |
| `devices` | no | Device name mapped to an object with a string `status`. Entries of any other shape are ignored. |

The probes ignore every other key, including the rest of the
[heartbeat payload](payloads.md). The file must be strict JSON: `NaN`,
`Infinity` and numbers that overflow to infinity make it unreadable.

## Checks

A probe reports the file **unhealthy** at the first of these that applies:

1. The file does not exist, cannot be read, or is not valid JSON.
2. The top-level value is not an object.
3. `health_file_version` is not an integer, or is a version the probe does not
   know. A newer writer therefore never passes an older probe.
4. `written_at` is missing or not a finite number.
5. The file is older than `--max-age`, which defaults to three times
   `interval`.
6. A device's `status` is listed in `--fail-on` (default: `stale`).

Otherwise the file is healthy.

## Versioning

The current version is `1`. Adding optional keys does not change the version,
because probes ignore keys they do not know. A change that an older probe would
misread, such as renaming `written_at` or changing its unit, needs a new major
version and probes that accept it.

## Probes

| Command | Installed by | Cost per run |
|---------|--------------|--------------|
| `cosalette-health` | every install of `cosalette` | about 1 ms and 2 MiB (native), about 60 ms and 15 MiB (Python fallback) |
| `cosalette health` | every install of `cosalette` | about 390 ms and 46 MiB |
| `myapp health` | your app's CLI | the app's import time on top of `cosalette health` |

All of them exit `0` when healthy and `1` otherwise, including on usage errors.
See [Health Probe](cli.md#health-probe) for the options and
[Health Checks](../guides/deployment.md#health-checks) for when to run a probe
at all.

!!! info "Native binary or Python fallback"

    Platform wheels contain `cosalette-health` as a native binary for Linux
    x86_64, aarch64 and armv7 (glibc 2.17 or newer), Alpine/musl x86_64 and
    aarch64, macOS x86_64 and arm64, and Windows x86_64. Everywhere else, for
    example Alpine on armv7, pip builds `cosalette` from the source
    distribution and `cosalette-health` is a Python script with the same
    options and exit codes that imports only the standard library.

    `head -c 4 "$(command -v cosalette-health)" | od -c` shows `177 E L F` for
    the native binary on Linux and `#!` for the Python script.
