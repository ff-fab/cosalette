---
title: Architecture Decision Records
description: ADRs documenting significant architectural decisions for cosalette
---

# Architecture Decision Records

This directory contains the Architecture Decision Records (ADRs) for the cosalette
framework. ADRs document significant architectural decisions with their context,
rationale, and consequences.

## ADR Index

| ADR | Title | Status | Date |
| --- | ----- | ------ | ---- |
| [ADR-001](ADR-001-framework-architecture-style.md) | Framework Architecture Style | Accepted | 2026-02-14 |
| [ADR-002](ADR-002-mqtt-topic-conventions.md) | MQTT Topic Conventions | Accepted | 2026-02-14 |
| [ADR-003](ADR-003-configuration-system.md) | Configuration System | Accepted | 2026-02-14 |
| [ADR-004](ADR-004-logging-strategy.md) | Logging Strategy | Accepted | 2026-02-14 |
| [ADR-005](ADR-005-cli-framework.md) | CLI Framework | Accepted | 2026-02-14 |
| [ADR-006](ADR-006-hexagonal-architecture.md) | Hexagonal Architecture (Ports & Adapters) | Accepted | 2026-02-14 |
| [ADR-007](ADR-007-testing-strategy.md) | Testing Strategy | Accepted | 2026-02-14 |
| [ADR-008](ADR-008-packaging-and-distribution.md) | Packaging and Distribution | Accepted | 2026-02-14 |
| [ADR-009](ADR-009-python-version-and-dependencies.md) | Python Version and Dependencies | Accepted | 2026-02-14 |
| [ADR-010](ADR-010-device-archetypes.md) | Device Archetypes | Accepted | 2026-02-14 |
| [ADR-011](ADR-011-error-handling-and-publishing.md) | Error Handling and Publishing | Accepted | 2026-02-14 |
| [ADR-012](ADR-012-health-and-availability-reporting.md) | Health and Availability Reporting | Accepted | 2026-02-14 |
| [ADR-013](ADR-013-telemetry-publish-strategies.md) | Telemetry Publish Strategies | Accepted | 2026-02-22 |
| [ADR-014](ADR-014-signal-filters.md) | Signal Filters | Accepted | 2026-02-22 |
| [ADR-015](ADR-015-persistence.md) | Persistence | Accepted | 2026-02-25 |
| [ADR-016](ADR-016-adapter-lifecycle-protocol.md) | Adapter Lifecycle Protocol | Accepted | 2026-02-26 |
| [ADR-017](ADR-017-sbom-generation.md) | SBOM Generation | Accepted | 2026-02-27 |
| [ADR-018](ADR-018-coalescing-groups.md) | Telemetry Coalescing Groups | Accepted | 2026-03-03 |
| [ADR-019](ADR-019-scoped-name-uniqueness.md) | Scoped Name Uniqueness | Accepted | 2026-03-04 |
| [ADR-020](ADR-020-deferred-interval-resolution.md) | Deferred Interval Resolution | Accepted | 2026-03-04 |
| [ADR-021](ADR-021-json-serialization.md) | JSON Serialisation | Accepted | 2026-03-07 |
| [ADR-022](ADR-022-rust-only-signal-filters.md) | Rust-Only Signal Filters | Accepted | 2026-03-09 |
| [ADR-023](ADR-023-on-configure-lifecycle-phase.md) | `on_configure` Lifecycle Phase and Dict-Name Device Registration | Accepted | 2026-03-31 |
| [ADR-024](ADR-024-telemetry-retry-backoff.md) | Telemetry Retry with Configurable Backoff | Accepted | 2026-03-31 |
| [ADR-025](ADR-025-command-channel-and-subtopic-routing.md) | Command Channel and Sub-Topic Routing | Accepted | 2026-03-31 |
| [ADR-026](ADR-026-immutable-releases.md) | Immutable Releases | Accepted | 2026-04-02 |
| [ADR-027](ADR-027-lifespan-yielded-di-state.md) | Lifespan-Yielded Injectable State | Superseded by ADR-039 | 2026-04-02 |
| [ADR-028](ADR-028-adapter-health-check-protocol.md) | Adapter Health Check Protocol | Accepted | 2026-04-02 |
| [ADR-029](ADR-029-adapter-auto-restart-strategy.md) | Adapter Auto-Restart Strategy | Accepted | 2026-04-03 |
| [ADR-030](ADR-030-documentation-hosting-strategy.md) | Documentation Hosting Strategy | Accepted | 2026-04-05 |
| [ADR-031](ADR-031-sub-entity-context-manager.md) | Sub-Entity Context Manager | Accepted | 2026-04-06 |
| [ADR-032](ADR-032-sleep-until-wall-clock-scheduling.md) | Cron Scheduling and Wall-Clock Sleep | Accepted | 2026-04-06 |
| [ADR-033](ADR-033-mqtt-schema-enforcement.md) | MQTT Schema Enforcement | Accepted | 2026-04-09 |
| [ADR-034](ADR-034-ai-friendly-downstream-framework-context.md) | AI-Friendly Downstream Framework Context | Accepted | 2026-04-12 |
| [ADR-035](ADR-035-optional-mcp-layer-for-downstream-ai-support.md) | Optional MCP Layer for Downstream AI Support | Accepted | 2026-04-14 |
| [ADR-036](ADR-036-triggerable-telemetry.md) | Triggerable Telemetry | Accepted | 2026-04-18 |
| [ADR-037](ADR-037-lazy-store-resolution.md) | Lazy Store Resolution | Accepted | 2026-04-20 |
| [ADR-038](ADR-038-deferred-enabled-for-decorator-registrations.md) | Deferred enabled= for Decorator Registrations | Accepted | 2026-04-20 |
| [ADR-039](ADR-039-app-state-factory.md) | @app.state Shared-State Factory | Accepted | 2026-04-25 |
| [ADR-040](ADR-040-command-sub-dispatch.md) | Command Sub-Dispatch | Accepted | 2026-04-25 |
| [ADR-041](ADR-041-periodic-background-tasks.md) | Periodic Background Tasks | Accepted | 2026-04-26 |
| [ADR-042](ADR-042-streaming-protocol-streamableport-and-stream-t.md) | Streaming Protocol: StreamablePort and Stream[T] | Accepted | 2026-04-26 |
| [ADR-043](ADR-043-domain-event-reactors-for-state-objects.md) | Domain-Event Reactors for State Objects | Accepted | 2026-05-04 |
| [ADR-044](ADR-044-public-router-and-composition-api.md) | Public Router and Composition API | Accepted | 2026-05-06 |
| [ADR-045](ADR-045-stateful-stream-receiver-semantics.md) | Stateful Stream Receiver Semantics | Accepted | 2026-05-08 |
| [ADR-046](ADR-046-typed-handler-contract-validation.md) | Typed Handler Contract Validation | Accepted | 2026-05-09 |
| [ADR-047](ADR-047-transport-availability-signaling.md) | Transport Availability Signaling | Accepted | 2026-06-23 |
| [ADR-048](ADR-048-clear-orphaned-retained-topics-for-removed-entities.md) | Clear orphaned retained topics for removed entities | Accepted | 2026-07-11 |
| [ADR-049](ADR-049-default-store-path-resolution.md) | Default store path resolution | Accepted | 2026-07-12 |
| [ADR-050](ADR-050-typed-consumer-producer-for-x-cosalette-consumer.md) | Typed consumer() Producer for x-cosalette-consumer | Accepted | 2026-07-27 |
| [ADR-051](ADR-051-settings-aware-schema-pipeline-for-settings-derived-entity-names.md) | Settings-Aware Schema Pipeline for Settings-Derived Entity Names | Accepted | 2026-08-04 |
| [ADR-052](ADR-052-relocatable-mcp-server-command-and-per-tool-claude-code-kilo-mcp-config-generation.md) | Relocatable MCP Server Command and Per-Tool (Claude Code / Kilo) MCP Config Generation | Accepted | 2026-08-05 |
