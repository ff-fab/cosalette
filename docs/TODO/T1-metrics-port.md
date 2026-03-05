# T1: MetricsPort Protocol for Observability

> **Status:** Deferred
> **Phase trigger:** Validate need via mqtt2prometheus bridge; implement when
> framework-internal metrics (publish counts, handler durations) are demonstrably needed
> **Related ADRs:** ADR-006, ADR-012, ADR-016
> **Gate task:** COS-5e1

## Problem Statement

The cosalette framework provides MQTT-native health reporting — heartbeat messages,
MQTT Last Will and Testament (LWT) for crash detection, and per-device availability
topics — as specified in ADR-012. This design ensures that liveness and availability
information flows through the same MQTT broker that carries telemetry data, avoiding
the need for a secondary transport. For small fleets and home-automation dashboards
(e.g., Home Assistant), this is sufficient: subscribers can directly consume
`cosalette/{app}/health/heartbeat` and `cosalette/{app}/device/{name}/availability`
topics.

However, professional observability stacks — Prometheus for metrics collection, Grafana
for dashboards, Alertmanager for alerting — expect an HTTP `/metrics` endpoint that
serves data in the OpenMetrics exposition format. ADR-012 explicitly rejected an HTTP
health endpoint because it "requires an HTTP server in what is otherwise a pure MQTT
application" and "adds network port management." While that reasoning holds for health
checks (which are well-served by LWT and heartbeats), metrics export is a distinct
concern: it enables time-series aggregation, histogram quantiles, and fleet-wide
queries that MQTT topic subscribers cannot provide.

The framework's `HealthReporter` already tracks useful internal state — monotonic
uptime via `_start_time`, per-device status via `_devices`, and application version.
But it does **not** track operational metrics that matter for production monitoring:
publish counts per device, error counts by type, MQTT reconnection events, telemetry
handler durations, or message throughput rates. Exposing these metrics requires both
(a) instrumentation points within the framework and (b) a port through which adapters
can export the collected data. This document evaluates the options for adding that
capability.

## Interim Solution: mqtt2prometheus Bridge

Before investing in framework-level metrics instrumentation, the existing MQTT health
topics can be bridged to Prometheus using
[mqtt2prometheus](https://github.com/hikhvar/mqtt2prometheus) — an external Go binary
that subscribes to MQTT topics, parses payloads, and exposes the extracted values as
Prometheus metrics on an HTTP endpoint.

This approach requires **zero framework changes**. It validates whether Prometheus-based
monitoring adds genuine value for a cosalette deployment before committing to protocol
design and instrumentation work. If mqtt2prometheus proves sufficient for operational
needs, the MetricsPort work can be deferred indefinitely.

### Example mqtt2prometheus configuration

```yaml
# mqtt2prometheus config for cosalette health topics
mqtt:
  server: tcp://localhost:1883
  topic_path: cosalette/+/health/#
  device_id_regex: "cosalette/(?P<app>[^/]+)/health/.*"
  qos: 0

cache:
  timeout: 5m

json_parsing:
  separator: .

metrics:
  - prom_name: cosalette_heartbeat_uptime_seconds
    mqtt_name: uptime_s
    help: "Application uptime in seconds from heartbeat payload"
    type: gauge
    const_labels:
      source: mqtt2prometheus

  - prom_name: cosalette_heartbeat_info
    mqtt_name: version
    help: "Application version from heartbeat payload"
    type: gauge
    string_value_mapping:
      map:
        "*": 1
      error_value: 0
```

### What this validates

- Whether the operations team actually queries Prometheus for cosalette data
- Whether heartbeat-derived uptime and availability are sufficient, or whether
  framework-internal counters (publishes, errors, handler latency) are needed
- Whether the deployment environment can support a Prometheus scrape target

If the answer to the last question is "we need publish counts, error breakdowns, and
handler duration histograms," then the framework must be instrumented — and the
mqtt2prometheus bridge cannot provide that data. That is the trigger for implementing
MetricsPort.

## Framework Metrics Catalogue

The following table enumerates the metrics the framework could expose if a MetricsPort
is implemented. Metrics marked **existing** can be derived from current internal state;
metrics marked **new** require adding instrumentation counters or timers.

| Metric name                            | Type      | Labels                  | Source                        | Status   |
| -------------------------------------- | --------- | ----------------------- | ----------------------------- | -------- |
| `cosalette_uptime_seconds`             | Gauge     | —                       | `HealthReporter._start_time`  | Existing |
| `cosalette_device_status`              | Gauge     | `device`                | `HealthReporter._devices`     | Existing |
| `cosalette_app_info`                   | Info      | `version`, `python`     | `HealthReporter`              | Existing |
| `cosalette_publishes_total`            | Counter   | `device`                | `DeviceContext.publish()`     | **New**  |
| `cosalette_publish_bytes_total`        | Counter   | `device`                | `DeviceContext.publish()`     | **New**  |
| `cosalette_errors_total`               | Counter   | `device`, `error_type`  | `ErrorPublisher`              | **New**  |
| `cosalette_handler_duration_seconds`   | Histogram | `device`                | `_run_telemetry` loop         | **New**  |
| `cosalette_mqtt_reconnects_total`      | Counter   | —                       | `MqttClient` reconnect hook   | **New**  |
| `cosalette_mqtt_messages_received`     | Counter   | `topic`                 | `MqttClient` message callback | **New**  |
| `cosalette_heartbeats_sent_total`      | Counter   | —                       | `HealthReporter`              | **New**  |

### Notes on instrumentation

- **Counters** (`_total` suffix) are monotonically increasing. Prometheus computes
  rates from counter deltas — the framework never resets them.
- **Histograms** (`_seconds` suffix) track distributions. The default Prometheus bucket
  boundaries (5 ms–10 s) are reasonable for telemetry handler durations on a Pi.
- **Gauges** reflect point-in-time values and can go up or down.
- All new counters should be incremented at the call site (e.g., inside
  `DeviceContext.publish()`) via the MetricsPort interface. If no adapter is registered,
  the `NullMetricsCollector` no-ops make this zero-cost.

## Option A: MetricsPort Protocol + Separate Adapter Package

Define `MetricsPort` as a narrow `@runtime_checkable` Protocol in the core `cosalette`
package, following the same pattern as `MqttPort`. The Prometheus-specific adapter lives
in a separate package (`cosalette-prometheus`) or behind an optional dependency group
(`cosalette[prometheus]`). A `NullMetricsCollector` ships as the default adapter,
ensuring zero overhead when metrics are not opted into.

### Protocol sketch

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsPort(Protocol):
    """Port for emitting application metrics to an observability backend."""

    def increment(self, name: str, value: float = 1, /, **labels: str) -> None:
        """Increment a counter metric."""
        ...

    def gauge(self, name: str, value: float, /, **labels: str) -> None:
        """Set a gauge metric to an absolute value."""
        ...

    def observe(self, name: str, value: float, /, **labels: str) -> None:
        """Record an observation in a histogram/summary metric."""
        ...
```

### NullMetricsCollector (default)

```python
class NullMetricsCollector:
    """No-op metrics collector. Registered when no adapter is provided."""

    def increment(self, name: str, value: float = 1, /, **labels: str) -> None:
        pass

    def gauge(self, name: str, value: float, /, **labels: str) -> None:
        pass

    def observe(self, name: str, value: float, /, **labels: str) -> None:
        pass
```

### PrometheusMetricsAdapter (separate package)

```python
from prometheus_client import Counter, Gauge, Histogram, start_http_server


class PrometheusMetricsAdapter:
    """Adapter that exposes metrics via prometheus_client HTTP server."""

    def __init__(self, port: int = 9100) -> None:
        self._port = port
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    async def __aenter__(self) -> "PrometheusMetricsAdapter":
        start_http_server(self._port)
        return self

    async def __aexit__(self, *exc: object) -> None:
        # Shutdown HTTP server (prometheus_client doesn't expose this cleanly;
        # may need a wrapper around aiohttp or ASGI)
        pass

    def increment(self, name: str, value: float = 1, /, **labels: str) -> None:
        counter = self._counters.setdefault(
            name, Counter(name, name, list(labels.keys()))
        )
        counter.labels(**labels).inc(value)

    def gauge(self, name: str, value: float, /, **labels: str) -> None:
        g = self._gauges.setdefault(
            name, Gauge(name, name, list(labels.keys()))
        )
        g.labels(**labels).set(value)

    def observe(self, name: str, value: float, /, **labels: str) -> None:
        h = self._histograms.setdefault(
            name, Histogram(name, name, list(labels.keys()))
        )
        h.labels(**labels).observe(value)
```

### Registration pattern (mirrors MqttPort)

```python
app = Cosalette("myapp")
app.adapter(MetricsPort, PrometheusMetricsAdapter(port=9100))
```

### Advantages

- **Follows ADR-006 hexagonal pattern exactly.** MetricsPort is a port Protocol; the
  Prometheus implementation is a swappable adapter. The composition root wires them.
- **No HTTP dependency in core.** The `cosalette` package remains a pure MQTT
  application — `prometheus_client` and any HTTP server live in the adapter package.
  This preserves ADR-012's design rationale.
- **ADR-016 lifecycle management fits perfectly.** `PrometheusMetricsAdapter` implements
  `__aenter__`/`__aexit__`, so the framework's `AsyncExitStack` auto-manages the HTTP
  server lifecycle — startup on app enter, graceful shutdown on exit.
- **Backend-agnostic.** The narrow Protocol interface supports multiple backends without
  framework changes: Prometheus, StatsD, OTLP push, InfluxDB line protocol, or even a
  simple JSON-over-MQTT metrics publisher.
- **Zero overhead when unused.** `NullMetricsCollector` method calls are trivial no-ops.
  On a Pi Zero 2 W where every CPU cycle matters, this is significant.
- **Testable via MockMetricsCollector.** Tests can assert that specific metrics were
  emitted with expected labels and values, following the `MockMqttClient` pattern.

### Disadvantages

- **Separate package to publish and maintain.** Versioning, CI, release automation for
  `cosalette-prometheus` adds maintenance burden — though an optional dependency group
  (`cosalette[prometheus]`) in the same repo reduces this.
- **Apps must explicitly wire the adapter.** No metrics by default; the user must know
  to register the adapter. Mitigated by documentation and a quickstart example.
- **Protocol surface area is a design decision.** Too narrow (only `increment`) and
  adapters must work around limitations. Too wide (timers, distributions, exemplars) and
  the Protocol becomes a leaky abstraction over Prometheus-specific concepts. The
  three-method interface (`increment`, `gauge`, `observe`) balances generality and
  simplicity, but may need iteration.
- **Lazy metric creation in the adapter sketch above is not production-grade.**
  `prometheus_client` expects consistent label sets per metric name. The adapter would
  need a metric registry or upfront declaration to avoid runtime errors when label sets
  differ across calls.

## Option B: Built-in HTTP Metrics Endpoint

The framework ships a `/metrics` endpoint directly, enabled via a constructor parameter
such as `Cosalette("myapp", metrics_port=9090)`. The HTTP server and Prometheus client
library are bundled dependencies.

### Advantages

- **Zero configuration for apps.** One parameter enables full Prometheus metrics. No
  separate package to install, no adapter wiring to understand.
- **Consistent across fleet.** Every cosalette app exposes the same metrics at the same
  endpoint path, simplifying Prometheus service discovery and scrape configuration.
- **Simpler codebase.** No Protocol indirection — framework code emits Prometheus
  metrics directly via `prometheus_client`.

### Disadvantages

- **Adds HTTP dependency to core.** Requires `aiohttp`, `uvicorn`, or
  `prometheus_client` as a direct dependency of the `cosalette` package. This pulls in
  transitive dependencies (multidict, yarl, etc.) onto resource-constrained targets.
- **Violates ADR-012's explicit reasoning.** ADR-012 rejected an HTTP health endpoint
  specifically because it "requires an HTTP server in what is otherwise a pure MQTT
  application" and "adds network port management." While metrics are a different concern
  than health checks, baking HTTP into core contradicts the stated architectural
  principle.
- **Violates Single Responsibility Principle.** The framework becomes responsible for
  both MQTT communication and HTTP serving — two different network transports with
  different failure modes, security considerations, and lifecycle requirements.
- **Resource overhead on constrained hardware.** On a Pi Zero 2 W (512 MB RAM, quad
  Cortex-A53), an always-running HTTP server consumes memory and CPU even when
  Prometheus is not scraping. This matters for battery-powered or thermally constrained
  deployments.
- **Port conflicts.** Each cosalette app on the same host needs a unique metrics port.
  This complicates multi-app deployments and requires port allocation management.

## Option C: OpenTelemetry SDK Integration

Use the OpenTelemetry Python SDK for push-based metrics export via OTLP (OpenTelemetry
Protocol). Metrics are pushed to a collector (e.g., `otel-collector`) rather than pulled
via HTTP scrape. The framework instruments using OTEL's Meter API.

### Advantages

- **Industry standard, vendor-neutral.** OpenTelemetry is the CNCF's converged
  observability standard, replacing both OpenCensus and OpenTracing. Investment here is
  future-proof.
- **Push-based — no inbound HTTP port needed.** Metrics are exported to a collector via
  OTLP/gRPC or OTLP/HTTP push. No listening socket on the cosalette app, which
  preserves the "no HTTP server" principle from ADR-012.
- **Unified signals.** OTEL supports metrics, traces, and logs in one SDK. Future
  tracing support (correlating MQTT message flows) comes "for free."
- **Broad backend support.** The collector can export to Prometheus, Datadog, Jaeger,
  Zipkin, and dozens of other backends without changing app code.

### Disadvantages

- **Heavy SDK.** `opentelemetry-sdk` pulls in `protobuf`, `googleapis-common-protos`,
  and optionally `grpcio`. On a Pi Zero 2 W, `grpcio` alone takes significant memory
  and has historically been painful to build on ARM.
- **Requires OTLP collector deployment.** The user must run `otel-collector` (or a
  compatible receiver) as infrastructure. For a home-automation setup with a single
  Raspberry Pi, this is a significant operational burden.
- **Overkill for small fleets.** A 3-device home setup does not need distributed tracing
  or multi-signal correlation. The complexity-to-value ratio is unfavorable.
- **SDK initialization complexity.** OTEL requires configuring a `MeterProvider`,
  `MetricReader`, and `MetricExporter` — significantly more ceremony than the
  three-method MetricsPort Protocol.
- **Moving target.** The OTEL Python SDK, while stable, still sees breaking changes in
  minor releases. Pinning versions and managing compatibility adds maintenance cost.

## Recommendation

**Option A — MetricsPort Protocol + separate adapter package.** This is the most
aligned with the existing architecture:

- **ADR-006 (hexagonal):** MetricsPort is a port Protocol; PrometheusMetricsAdapter is a
  pluggable adapter. The framework remains the composition root.
- **ADR-012 (pure MQTT core):** No HTTP dependency in the core package. The metrics HTTP
  server lives entirely in the adapter.
- **ADR-016 (adapter lifecycle):** The `__aenter__`/`__aexit__` protocol manages the
  HTTP server lifecycle through the existing `AsyncExitStack` mechanism — no new
  lifecycle machinery needed.

The three-method Protocol interface (`increment`, `gauge`, `observe`) is deliberately
minimal. It maps cleanly to Prometheus metric types (Counter, Gauge, Histogram) while
remaining generic enough for StatsD or OTLP push backends. If OTEL becomes desirable
later, an `OtelMetricsAdapter` can implement the same Protocol — no framework changes
required.

### Recommended sequence

1. **Validate need with mqtt2prometheus bridge** (no framework changes). Deploy the
   bridge, configure Prometheus scraping, build Grafana dashboards. Determine whether
   MQTT-derived metrics are sufficient or whether framework-internal counters are
   genuinely needed.
2. **When validated:** Define `MetricsPort` Protocol in `cosalette`, add
   `NullMetricsCollector` as default, instrument framework internals (publish counts,
   error counts, handler durations, reconnection events).
3. **Ship adapter package.** Either `cosalette-prometheus` as a separate package, or
   `cosalette[prometheus]` as an optional dependency group in the same repository.
   Decision on packaging approach can be deferred to implementation time.
4. **Create ADR** documenting the decision, referencing this deliberation document and
   the mqtt2prometheus validation results.

## Acceptance Criteria (for gate task COS-5e1)

- [ ] mqtt2prometheus bridge has been deployed and evaluated against a running cosalette
      application
- [ ] Specific metrics gaps identified that require framework-level instrumentation
      (e.g., "need publish counts per device" or "need handler duration percentiles")
- [ ] MetricsPort Protocol designed, reviewed, and agreed upon
- [ ] Framework instrumentation points identified and implemented
- [ ] NullMetricsCollector and at least one real adapter (Prometheus) implemented
- [ ] Decision documented as a formal ADR (ADR-02x)
