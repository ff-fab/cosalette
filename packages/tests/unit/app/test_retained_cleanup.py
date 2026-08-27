"""Unit tests for ADR-048 orphaned retained-topic cleanup.

Covers:
  - build_entity_snapshot: correct structure, retained_kinds per type, merging.
  - reconcile_retained_topics: no-op without store, first-run, removal, edge cases.
  - Integration: connect-aware path (first-connect triggers, reconnect does not).
  - Integration: eager path (publish_startup_snapshot with non-connect-aware client).

Test Techniques:
    - State-based Testing: inspect MockMqttClient.published after reconcile.
    - Boundary-value Analysis: empty store, malformed snapshot, wrong schema version.
    - Security/scope guard: only state/availability are ever cleared.
    - Thread/Concurrency Testing: store I/O offloading via asyncio.to_thread.
    - Error Guessing: fail-closed contract against backend exceptions.
    - Round-trip Testing: SqliteStore snapshot persist/load fidelity.
    - Security Testing: HMAC-signed snapshots (ADR-063, F-DP3) — round-trip,
      wrong-key/tampered-payload/unrecognized-algorithm rejection, and the
      unsigned-vs-signed compatibility fail-closed decision.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast, override

import pytest
from pydantic import SecretStr

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttPort
from cosalette._persistence._stores import MemoryStore, SqliteStore, Store
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._wiring import _retained_cleanup as _retained_cleanup_module
from cosalette._wiring import (
    publish_startup_snapshot,
    register_connect_reannounce,
)
from cosalette._wiring._retained_cleanup import (
    _HMAC_ALG,
    _SNAPSHOT_SCHEMA_VERSION,
    _sign_snapshot,
    _snapshot_key,
    _snapshot_signature_valid,
    build_entity_snapshot,
    reconcile_retained_topics,
)
from cosalette.testing import FakeClock, MockMqttClient
from tests.fixtures.mqtt import FakeConnectAwareMqttClient

pytestmark = pytest.mark.unit

PREFIX = "testapp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clears(mqtt: MockMqttClient | FakeConnectAwareMqttClient) -> list[str]:
    """Return topics for which an empty retained publish was recorded."""
    return [t for (t, p, r, q) in mqtt.published if p == "" and r is True]


def _make_reporter(
    mqtt: MockMqttClient | FakeConnectAwareMqttClient,
) -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=cast(MqttPort, mqtt),
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


def _make_device_reg(name: str, *, is_root: bool = False) -> _DeviceRegistration:
    """Construct a minimal _DeviceRegistration for unit testing."""

    async def _stub() -> AsyncIterator[None]:  # pragma: no cover
        yield

    return _DeviceRegistration(
        name=name,
        func=_stub,  # type: ignore[arg-type]
        injection_plan=[],
        is_root=is_root,
    )


def _make_telemetry_reg(name: str, *, is_root: bool = False) -> _TelemetryRegistration:
    """Construct a minimal _TelemetryRegistration for unit testing."""

    async def _stub() -> dict[str, object]:  # pragma: no cover
        return {}

    return _TelemetryRegistration(
        name=name,
        func=_stub,
        injection_plan=[],
        interval=60.0,
        is_root=is_root,
    )


def _make_command_reg(name: str, *, is_root: bool = False) -> _CommandRegistration:
    """Construct a minimal _CommandRegistration for unit testing."""

    async def _stub() -> None:  # pragma: no cover
        pass

    return _CommandRegistration(
        name=name,
        func=_stub,  # type: ignore[arg-type]
        injection_plan=[],
        mqtt_params=frozenset(),
        is_root=is_root,
    )


# ---------------------------------------------------------------------------
# build_entity_snapshot
# ---------------------------------------------------------------------------


class TestBuildEntitySnapshot:
    """Unit tests for build_entity_snapshot()."""

    def test_device_entry_has_state_and_availability(self) -> None:
        """A device registration has retained_kinds with both state and availability."""
        snap = build_entity_snapshot([_make_device_reg("alpha")])
        entities = cast(dict[str, object], snap["entities"])
        entry = cast(dict[str, object], entities["alpha"])
        assert isinstance(entry, dict)
        assert entry["is_root"] is False
        assert set(cast(list[str], entry["retained_kinds"])) == {
            "state",
            "availability",
        }

    def test_command_only_entry_has_availability_only(self) -> None:
        """A command-only registration produces retained_kinds == ['availability']."""
        snap = build_entity_snapshot([_make_command_reg("gamma")])
        entities = cast(dict[str, object], snap["entities"])
        entry = cast(dict[str, object], entities["gamma"])
        assert isinstance(entry, dict)
        assert cast(list[str], entry["retained_kinds"]) == ["availability"]

    def test_telemetry_and_command_same_name_merges_kinds(self) -> None:
        """Telemetry + command sharing a name merges retained_kinds to both kinds."""
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [
            _make_telemetry_reg("shared"),
            _make_command_reg("shared"),
        ]
        snap = build_entity_snapshot(regs)
        entities = cast(dict[str, object], snap["entities"])
        entry = cast(dict[str, object], entities["shared"])
        assert isinstance(entry, dict)
        assert set(cast(list[str], entry["retained_kinds"])) == {
            "state",
            "availability",
        }

    def test_snapshot_has_schema_version_and_entities(self) -> None:
        """Snapshot has schema_version == _SNAPSHOT_SCHEMA_VERSION and entities dict."""
        snap = build_entity_snapshot([_make_device_reg("x")])
        assert snap["schema_version"] == _SNAPSHOT_SCHEMA_VERSION
        assert isinstance(snap["entities"], dict)


# ---------------------------------------------------------------------------
# reconcile_retained_topics
# ---------------------------------------------------------------------------


class TestReconcileRetainedTopics:
    """Unit tests for reconcile_retained_topics()."""

    async def test_no_store_no_publishes(self) -> None:
        """When store is None, no publishes occur and no exception is raised."""
        mqtt = MockMqttClient()
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, None)
        assert mqtt.publish_count == 0

    async def test_first_run_empty_store_no_clears(self) -> None:
        """First run with empty store produces zero clears; snapshot is persisted."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]
        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        assert _clears(mqtt) == []
        saved = store.load(_snapshot_key(PREFIX))
        assert saved is not None
        assert "entities" in saved
        entities = saved["entities"]
        assert isinstance(entities, dict)
        assert "alpha" in entities

    async def test_removed_named_device_clears_state_and_availability(self) -> None:
        """A removed named device produces two clears: state and availability."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "alpha": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )
        # Current run: alpha is absent
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        assert sorted(cleared) == sorted(
            [f"{PREFIX}/alpha/state", f"{PREFIX}/alpha/availability"]
        )
        # Verify each clear is retained=True, qos=1, payload=""
        for topic in cleared:
            msgs = [(p, r, q) for (t, p, r, q) in mqtt.published if t == topic]
            assert msgs == [("", True, 1)]

    async def test_removed_command_clears_availability_only(self) -> None:
        """A removed command produces exactly one clear (availability).

        No state topic is cleared for command-only entities.
        """
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "gamma": {"is_root": False, "retained_kinds": ["availability"]}
                },
            },
        )
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        assert cleared == [f"{PREFIX}/gamma/availability"]
        assert f"{PREFIX}/gamma/state" not in cleared

    async def test_removed_root_device_clears_root_topics(self) -> None:
        """A removed root device clears root-level state and availability.

        {prefix}/state and {prefix}/availability are cleared; NOT {prefix}/{name}/...
        """
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "rootdev": {
                        "is_root": True,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        assert sorted(cleared) == sorted([f"{PREFIX}/state", f"{PREFIX}/availability"])
        assert f"{PREFIX}/rootdev/state" not in cleared

    async def test_entity_still_present_not_cleared(self) -> None:
        """An entity present in both previous and current snapshot is NOT cleared."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "alpha": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )
        # Current run still has alpha
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]
        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        assert _clears(mqtt) == []

    async def test_invalid_name_in_snapshot_skipped_no_exception(self) -> None:
        """Invalid entity name in snapshot is skipped; valid removals still proceed."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "bad/name": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    },
                    "gooddev": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    },
                },
            },
        )
        # No current registrations
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        # bad/name is skipped; gooddev is cleared
        assert f"{PREFIX}/gooddev/state" in cleared
        assert f"{PREFIX}/gooddev/availability" in cleared
        # Nothing containing "bad/name" literally as a path component
        assert not any("bad/name" in t for t in cleared)

    async def test_wrong_schema_version_no_clears_snapshot_overwritten(self) -> None:
        """Wrong schema_version: no clears, store overwritten with v1 snapshot."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": 999,
                "entities": {
                    "alpha": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("beta")]
        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        assert _clears(mqtt) == []
        saved = store.load(_snapshot_key(PREFIX))
        assert saved is not None
        assert saved["schema_version"] == _SNAPSHOT_SCHEMA_VERSION

    async def test_malformed_entities_not_dict_no_clears_no_exception(self) -> None:
        """Previous snapshot with entities not a dict → no clears, no exception."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": "not-a-dict",
            },
        )
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        assert _clears(mqtt) == []

    async def test_non_dict_snapshot_no_clears_snapshot_overwritten(self) -> None:
        """A corrupted non-dict snapshot from load() → no clears, no error, overwrite.

        Guards the fail-closed contract: store.load() returning a *truthy* non-dict
        (e.g. a list from an externally corrupted JSON file) must not raise, must
        not clear anything, and must still overwrite with a valid v1 snapshot so
        the app is not stuck on the bad payload forever.
        """
        mqtt = MockMqttClient()

        class _CorruptStore:
            """Store whose load() returns a corrupted non-dict payload."""

            def __init__(self) -> None:
                self.saved: dict[str, object] | None = None

            def load(self, key: str) -> dict[str, object] | None:  # noqa: ARG002
                return cast("dict[str, object] | None", [1, 2, 3])

            def save(self, key: str, data: dict[str, object]) -> None:  # noqa: ARG002
                self.saved = data

        store = _CorruptStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]
        await reconcile_retained_topics(
            cast(MqttPort, mqtt), regs, PREFIX, cast(Store, store)
        )

        assert _clears(mqtt) == []
        assert store.saved is not None
        assert store.saved["schema_version"] == _SNAPSHOT_SCHEMA_VERSION

    async def test_is_root_non_bool_value_not_treated_as_root(self) -> None:
        """A non-bool is_root value (string "False") is NOT treated as root.

        Defense against a tampered snapshot: only a real bool True marks a root
        device, so a corrupted truthy is_root must clear {prefix}/{name}/... and
        never widen scope to the root-level {prefix}/state.
        """
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "mydev": {
                        "is_root": "False",  # tampered non-bool truthy string
                        "retained_kinds": ["state", "availability"],
                    },
                },
            },
        )
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        assert sorted(cleared) == sorted(
            [f"{PREFIX}/mydev/state", f"{PREFIX}/mydev/availability"]
        )
        assert f"{PREFIX}/state" not in cleared
        assert f"{PREFIX}/availability" not in cleared

    async def test_scope_guard_only_state_and_availability_cleared(self) -> None:
        """Security: only state/availability cleared; /set /status /error skipped."""
        mqtt = MockMqttClient()
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "mydev": {
                        "is_root": False,
                        "retained_kinds": [
                            "state",
                            "availability",
                            "set",
                            "status",
                            "error",
                        ],
                    },
                },
            },
        )
        await reconcile_retained_topics(cast(MqttPort, mqtt), [], PREFIX, store)

        cleared = _clears(mqtt)
        # Only state/availability are cleared; set/status/error are ignored.
        for t in cleared:
            assert not t.endswith("/set"), f"Unexpected /set clear: {t}"
            assert not t.endswith("/status"), f"No /status: {t}"
            assert not t.endswith("/error"), f"No /error: {t}"
            assert "/_meta/" not in t, f"No /_meta/: {t}"
            assert "/schema/" not in t, f"No /schema/: {t}"
        assert sorted(cleared) == sorted(
            [f"{PREFIX}/mydev/state", f"{PREFIX}/mydev/availability"]
        )

    async def test_store_io_offloaded_to_worker_thread(self) -> None:
        """store.load and store.save run in a worker thread, not the event-loop thread.

        Regression for ADR-048: both calls must be offloaded via asyncio.to_thread
        so the event loop is never blocked by backend I/O.

        Technique: Error Guessing + Specification-based Testing — anticipating
        event-loop blocking when I/O is called without offloading; verifying the
        asyncio.to_thread contract stated in the function docstring.
        Note: asyncio.to_thread dispatches via ThreadPoolExecutor; the worker thread
        always has a different OS ident from the event-loop thread.
        """
        # Arrange
        main_ident = threading.get_ident()
        load_ident: int | None = None
        save_ident: int | None = None

        class _TrackingStore(MemoryStore):
            @override
            def load(self, key: str) -> dict[str, object] | None:
                nonlocal load_ident
                load_ident = threading.get_ident()
                return super().load(key)

            @override
            def save(self, key: str, data: dict[str, object]) -> None:
                nonlocal save_ident
                save_ident = threading.get_ident()
                super().save(key, data)

        mqtt = MockMqttClient()
        store = _TrackingStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]

        # Act
        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        # Assert
        assert load_ident is not None, "store.load was never called"
        assert save_ident is not None, "store.save was never called"
        assert load_ident != main_ident, "store.load ran on the event-loop thread"
        assert save_ident != main_ident, "store.save ran on the event-loop thread"

    async def test_store_load_raises_exception_is_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """store.load() raising is caught and logged — startup is never interrupted.

        Technique: Error Guessing — verifying the fail-closed contract against
        unexpected backend failures (e.g. SqliteStore cross-thread errors).
        """

        # Arrange
        class _RaisingStore(MemoryStore):
            @override
            def load(self, key: str) -> dict[str, object] | None:
                raise RuntimeError("backend unavailable")

        mqtt = MockMqttClient()
        store = _RaisingStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]

        # Act — must not raise
        with caplog.at_level(logging.ERROR):
            await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        # Assert
        assert mqtt.publish_count == 0
        assert any("reconciliation failed" in r.message for r in caplog.records)

    async def test_store_save_raises_exception_is_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """store.save() raising is caught and logged — startup is never interrupted.

        Technique: Error Guessing — verifying the fail-closed contract for the
        save path (store.load succeeds, store.save raises).
        """

        # Arrange
        class _SaveRaisingStore(MemoryStore):
            @override
            def save(self, key: str, data: dict[str, object]) -> None:
                raise RuntimeError("disk full")

        mqtt = MockMqttClient()
        store = _SaveRaisingStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]

        # Act — must not raise
        with caplog.at_level(logging.ERROR):
            await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        # Assert
        assert any("reconciliation failed" in r.message for r in caplog.records)

    async def test_reconcile_with_sqlite_store_saves_and_clears(
        self, tmp_path: Path
    ) -> None:
        """reconcile_retained_topics works end-to-end with a real SqliteStore.

        Regression for SqliteStore check_same_thread: verifies that store.load
        and store.save succeed from a worker thread (asyncio.to_thread) and that
        the snapshot round-trips correctly.

        Technique: Round-trip Testing + Error Guessing — confirming SqliteStore
        survives cross-thread access and persists/loads the entity snapshot.
        """
        # Arrange
        store = SqliteStore(tmp_path / "test.db")
        mqtt = MockMqttClient()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]

        # Act — must not raise; snapshot must be persisted
        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        # Assert
        saved = store.load(_snapshot_key(PREFIX))
        assert saved is not None, "SqliteStore should have persisted the snapshot"
        assert "entities" in saved
        assert "alpha" in cast(dict[str, object], saved["entities"])
        assert mqtt.publish_count == 0  # first run: nothing to clear
        store.close()


# ---------------------------------------------------------------------------
# HMAC-signed snapshots (ADR-063, F-DP3)
# ---------------------------------------------------------------------------

_KEY_A = b"correct-signing-key-a"
_KEY_B = b"different-signing-key-b"


class TestSnapshotSigningUnit:
    """Unit tests for _sign_snapshot / _snapshot_signature_valid (ADR-063).

    Technique: Security Testing — round-trip verification, tamper detection
    (payload and digest), and the forward-compat unrecognized-algorithm guard.
    """

    def test_sign_snapshot_adds_hmac_fields(self) -> None:
        """_sign_snapshot adds hmac_alg == _HMAC_ALG and a hex hmac_sha256 digest."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])

        signed = _sign_snapshot(snapshot, _KEY_A)

        assert signed["hmac_alg"] == _HMAC_ALG
        assert isinstance(signed["hmac_sha256"], str)
        assert len(signed["hmac_sha256"]) == 64  # sha256 hex digest

    def test_sign_snapshot_does_not_mutate_original(self) -> None:
        """_sign_snapshot returns a copy; the input snapshot dict is untouched."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])

        _sign_snapshot(snapshot, _KEY_A)

        assert "hmac_alg" not in snapshot
        assert "hmac_sha256" not in snapshot

    def test_snapshot_signature_valid_true_for_freshly_signed_snapshot(self) -> None:
        """A snapshot signed with key K verifies successfully against key K."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        signed = _sign_snapshot(snapshot, _KEY_A)

        assert _snapshot_signature_valid(signed, _KEY_A) is True

    def test_snapshot_signature_valid_false_for_wrong_key(self) -> None:
        """Verification against a different key than the one used to sign fails."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        signed = _sign_snapshot(snapshot, _KEY_A)

        assert _snapshot_signature_valid(signed, _KEY_B) is False

    def test_snapshot_signature_valid_false_for_tampered_entities(self) -> None:
        """Mutating `entities` after signing invalidates the digest."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        signed = _sign_snapshot(snapshot, _KEY_A)
        tampered = {**signed, "entities": {"injected": {"is_root": True}}}

        assert _snapshot_signature_valid(tampered, _KEY_A) is False

    def test_snapshot_signature_valid_false_for_tampered_digest(self) -> None:
        """Flipping a character of the stored hmac_sha256 digest invalidates it."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        signed = _sign_snapshot(snapshot, _KEY_A)
        original = cast(str, signed["hmac_sha256"])
        flipped = ("0" if original[0] != "0" else "1") + original[1:]
        tampered = {**signed, "hmac_sha256": flipped}

        assert _snapshot_signature_valid(tampered, _KEY_A) is False

    def test_snapshot_signature_valid_false_for_unrecognized_hmac_alg(self) -> None:
        """An unrecognized hmac_alg is rejected before any digest is computed.

        Forward-compat guard (ADR-063): a future/unknown algorithm identifier
        must never reach verification logic that does not understand it.
        """
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        signed = _sign_snapshot(snapshot, _KEY_A)
        # Swap the alg selector but keep the (now-irrelevant) original digest.
        tampered = {**signed, "hmac_alg": "hmac-sha1"}

        assert _snapshot_signature_valid(tampered, _KEY_A) is False

    def test_snapshot_signature_valid_false_for_missing_hmac_sha256(self) -> None:
        """A recognized hmac_alg with no hmac_sha256 field is rejected, not raised."""
        snapshot = build_entity_snapshot([_make_device_reg("alpha")])
        malformed = {**snapshot, "hmac_alg": _HMAC_ALG}

        assert _snapshot_signature_valid(malformed, _KEY_A) is False

    def test_canonical_signed_payload_order_independent(self) -> None:
        """Canonical JSON (sort_keys=True) is identical regardless of dict order.

        Confirms the digest is stable across insertion-order differences a
        Store round-trip (e.g. JSON decode) could introduce.
        """
        payload_1 = _retained_cleanup_module._canonical_signed_payload(
            _HMAC_ALG, 1, {"b": 2, "a": 1}
        )
        payload_2 = _retained_cleanup_module._canonical_signed_payload(
            _HMAC_ALG, 1, {"a": 1, "b": 2}
        )

        assert payload_1 == payload_2


class TestReconcileHmacSignedSnapshots:
    """Integration tests: reconcile_retained_topics with snapshot_key (ADR-063).

    Technique: Security Testing + Round-trip Testing — signed persist/verify
    across successive reconcile runs, and fail-closed behavior on every way a
    signature can fail to validate (wrong key, tampered payload, tampered
    digest, unrecognized algorithm, and a pre-existing unsigned snapshot).
    """

    async def test_reconcile_no_key_saved_snapshot_has_no_hmac_fields(self) -> None:
        """Default (snapshot_key=None) preserves legacy unsigned behavior exactly.

        No hmac_alg/hmac_sha256 fields are written when no key is configured.
        """
        mqtt = MockMqttClient()
        store = MemoryStore()
        regs: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]

        await reconcile_retained_topics(cast(MqttPort, mqtt), regs, PREFIX, store)

        saved = store.load(_snapshot_key(PREFIX))
        assert saved is not None
        assert "hmac_alg" not in saved
        assert "hmac_sha256" not in saved

    async def test_reconcile_signed_round_trip_diffs_correctly_across_runs(
        self,
    ) -> None:
        """Save-then-load with the correct key verifies and diffs correctly.

        Run 1 (keyed): alpha present, snapshot signed. Run 2 (same key): alpha
        removed — the signature verifies, so the removal is detected and
        alpha's retained topics are cleared, and the freshly saved snapshot is
        itself validly signed.
        """
        store = MemoryStore()
        key = SecretStr("correct-signing-key-a")

        # Run 1: alpha present.
        mqtt1 = MockMqttClient()
        regs1: list[
            _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
        ] = [_make_device_reg("alpha")]
        await reconcile_retained_topics(
            cast(MqttPort, mqtt1), regs1, PREFIX, store, key
        )
        assert _clears(mqtt1) == []
        saved_1 = store.load(_snapshot_key(PREFIX))
        assert saved_1 is not None
        assert _snapshot_signature_valid(saved_1, _KEY_A) is True

        # Run 2: alpha removed, same key.
        mqtt2 = MockMqttClient()
        await reconcile_retained_topics(cast(MqttPort, mqtt2), [], PREFIX, store, key)

        cleared = _clears(mqtt2)
        assert sorted(cleared) == sorted(
            [f"{PREFIX}/alpha/state", f"{PREFIX}/alpha/availability"]
        )
        saved_2 = store.load(_snapshot_key(PREFIX))
        assert saved_2 is not None
        assert _snapshot_signature_valid(saved_2, _KEY_A) is True

    async def test_reconcile_wrong_key_treated_as_absent_no_clears(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verification failure on the wrong key is treated as no previous snapshot.

        Fail-closed: the same code path as "no previous snapshot" — this
        run's cleanup is skipped rather than trusting an unverifiable diff.
        """
        store = MemoryStore()
        signed = _sign_snapshot(
            build_entity_snapshot([_make_device_reg("alpha")]), _KEY_A
        )
        store.save(_snapshot_key(PREFIX), signed)

        mqtt = MockMqttClient()
        wrong_key = SecretStr("different-signing-key-b")
        with caplog.at_level(logging.WARNING):
            await reconcile_retained_topics(
                cast(MqttPort, mqtt), [], PREFIX, store, wrong_key
            )

        assert _clears(mqtt) == []
        assert any("HMAC verification" in r.message for r in caplog.records)

    async def test_reconcile_tampered_entities_treated_as_absent_no_clears(
        self,
    ) -> None:
        """A tampered `entities` field invalidates the digest → fail-closed."""
        store = MemoryStore()
        signed = _sign_snapshot(
            build_entity_snapshot([_make_device_reg("alpha")]), _KEY_A
        )
        tampered = {
            **signed,
            "entities": {"injected": {"is_root": False, "retained_kinds": ["state"]}},
        }
        store.save(_snapshot_key(PREFIX), tampered)

        mqtt = MockMqttClient()
        await reconcile_retained_topics(
            cast(MqttPort, mqtt), [], PREFIX, store, SecretStr("correct-signing-key-a")
        )

        # No clears at all: neither "alpha" (no longer in the trusted diff)
        # nor "injected" (never validated, never diffed).
        assert _clears(mqtt) == []

    async def test_reconcile_tampered_digest_treated_as_absent_no_clears(
        self,
    ) -> None:
        """A tampered `hmac_sha256` digest is rejected → fail-closed."""
        store = MemoryStore()
        signed = _sign_snapshot(
            build_entity_snapshot([_make_device_reg("alpha")]), _KEY_A
        )
        original = cast(str, signed["hmac_sha256"])
        flipped = ("0" if original[0] != "0" else "1") + original[1:]
        tampered = {**signed, "hmac_sha256": flipped}
        store.save(_snapshot_key(PREFIX), tampered)

        mqtt = MockMqttClient()
        await reconcile_retained_topics(
            cast(MqttPort, mqtt), [], PREFIX, store, SecretStr("correct-signing-key-a")
        )

        assert _clears(mqtt) == []

    async def test_reconcile_unrecognized_hmac_alg_rejected_before_verification(
        self,
    ) -> None:
        """An unrecognized hmac_alg is rejected before digest verification is attempted.

        Forward-compat guard (ADR-063): treated as no previous snapshot, same
        fail-closed path as every other verification failure.
        """
        store = MemoryStore()
        signed = _sign_snapshot(
            build_entity_snapshot([_make_device_reg("alpha")]), _KEY_A
        )
        tampered = {**signed, "hmac_alg": "hmac-sha1"}
        store.save(_snapshot_key(PREFIX), tampered)

        mqtt = MockMqttClient()
        await reconcile_retained_topics(
            cast(MqttPort, mqtt), [], PREFIX, store, SecretStr("correct-signing-key-a")
        )

        assert _clears(mqtt) == []

    async def test_reconcile_unsigned_snapshot_with_key_configured_treated_as_absent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A pre-existing *unsigned* snapshot is fail-closed once a key is configured.

        Intended behavior per ADR-063's Decision section: "on any mismatch,
        missing signature field (e.g. a snapshot written before the key was
        configured, or written by an unkeyed run) ... the loaded snapshot is
        treated as absent". An unsigned snapshot has no hmac_alg field, so
        `_snapshot_signature_valid` returns False (hmac_alg != _HMAC_ALG) —
        it is NOT silently trusted just because it is otherwise well-formed.
        This is the correct fail-closed choice: an app newly adopting a key
        must not have stale unauthenticated data treated as verified. This
        run's cleanup is skipped and the snapshot is overwritten with a
        freshly signed one, exactly as the module docstring documents.
        """
        store = MemoryStore()
        # Legacy unsigned snapshot, written before any key was configured.
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "alpha": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )

        mqtt = MockMqttClient()
        with caplog.at_level(logging.WARNING):
            await reconcile_retained_topics(
                cast(MqttPort, mqtt),
                [],  # alpha removed this run
                PREFIX,
                store,
                SecretStr("correct-signing-key-a"),
            )

        # Fail-closed: cleanup skipped, not "trust the unsigned data".
        assert _clears(mqtt) == []
        assert any("HMAC verification" in r.message for r in caplog.records)

        # The stored snapshot is now freshly signed for future runs.
        saved = store.load(_snapshot_key(PREFIX))
        assert saved is not None
        assert _snapshot_signature_valid(saved, _KEY_A) is True


# ---------------------------------------------------------------------------
# Integration — connect-aware path
# ---------------------------------------------------------------------------


class TestIntegrationConnectAwarePath:
    """Integration: reconcile runs on first connect, not on reconnect."""

    async def test_removed_entity_cleared_on_first_connect(self) -> None:
        """Run 1: alpha+beta. Run 2: alpha only. beta cleared on run-2 first connect."""
        store = MemoryStore()

        # --- Run 1: alpha + beta ---
        fake1 = FakeConnectAwareMqttClient()
        app1 = App(name=PREFIX, version="1.0.0")

        @app1.device("alpha")
        async def _d1(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        @app1.device("beta")
        async def _d2(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter1 = _make_reporter(fake1)
        register_connect_reannounce(
            fake1, app1, reporter1, app1._all_registrations, PREFIX, store
        )
        await fake1.simulate_connect()

        # --- Run 2: alpha only ---
        fake2 = FakeConnectAwareMqttClient()
        app2 = App(name=PREFIX, version="1.0.0")

        @app2.device("alpha")
        async def _d3(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter2 = _make_reporter(fake2)
        register_connect_reannounce(
            fake2, app2, reporter2, app2._all_registrations, PREFIX, store
        )
        await fake2.simulate_connect()

        cleared = _clears(fake2)
        assert f"{PREFIX}/beta/state" in cleared
        assert f"{PREFIX}/beta/availability" in cleared

        # alpha still present — must NOT be cleared
        assert f"{PREFIX}/alpha/state" not in cleared
        assert f"{PREFIX}/alpha/availability" not in cleared

    async def test_reconnect_does_not_re_clear_removed_entity(self) -> None:
        """Reconnect (non-initial connect) does NOT trigger reconcile again."""
        store = MemoryStore()

        # Run 1: seed store with alpha+beta
        fake1 = FakeConnectAwareMqttClient()
        app1 = App(name=PREFIX, version="1.0.0")

        @app1.device("alpha")
        async def _a1(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        @app1.device("beta")
        async def _b1(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter1 = _make_reporter(fake1)
        register_connect_reannounce(
            fake1, app1, reporter1, app1._all_registrations, PREFIX, store
        )
        await fake1.simulate_connect()

        # Run 2: alpha only, first connect
        fake2 = FakeConnectAwareMqttClient()
        app2 = App(name=PREFIX, version="1.0.0")

        @app2.device("alpha")
        async def _a2(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter2 = _make_reporter(fake2)
        register_connect_reannounce(
            fake2, app2, reporter2, app2._all_registrations, PREFIX, store
        )
        await fake2.simulate_connect()

        beta_clears_after_first_connect = [t for t in _clears(fake2) if "beta" in t]
        assert len(beta_clears_after_first_connect) == 2  # state + availability

        # Reconnect (second simulate_connect on the same fake2)
        fake2.reset()
        await fake2.simulate_connect()

        # No additional beta clears on reconnect
        beta_clears_after_reconnect = [t for t in _clears(fake2) if "beta" in t]
        assert beta_clears_after_reconnect == []


# ---------------------------------------------------------------------------
# Integration — eager (non-connect-aware) path
# ---------------------------------------------------------------------------


class TestIntegrationEagerPath:
    """Integration: publish_startup_snapshot triggers reconcile (non-connect-aware)."""

    async def test_removed_entity_cleared_via_eager_path(self) -> None:
        """publish_startup_snapshot clears a removed entity for MockMqttClient path."""
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "removed_dev": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )

        mqtt = MockMqttClient()
        app = App(name=PREFIX, version="1.0.0")

        @app.device("alpha")
        async def _alpha(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter = _make_reporter(mqtt)

        await publish_startup_snapshot(
            app,
            cast(MqttPort, mqtt),
            reporter,
            app._all_registrations,
            PREFIX,
            store,
            connect_aware=False,
        )

        cleared = _clears(mqtt)
        assert f"{PREFIX}/removed_dev/state" in cleared
        assert f"{PREFIX}/removed_dev/availability" in cleared

    async def test_connect_aware_true_no_op(self) -> None:
        """publish_startup_snapshot is a no-op when connect_aware=True."""
        store = MemoryStore()
        store.save(
            _snapshot_key(PREFIX),
            {
                "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                "entities": {
                    "removed_dev": {
                        "is_root": False,
                        "retained_kinds": ["state", "availability"],
                    }
                },
            },
        )

        mqtt = MockMqttClient()
        app = App(name=PREFIX, version="1.0.0")

        @app.device("alpha")
        async def _d4(ctx: DeviceContext) -> AsyncIterator[None]:  # pragma: no cover
            yield

        reporter = _make_reporter(mqtt)

        await publish_startup_snapshot(
            app,
            cast(MqttPort, mqtt),
            reporter,
            app._all_registrations,
            PREFIX,
            store,
            connect_aware=True,
        )

        # connect_aware=True means entire function is a no-op — zero publishes
        assert mqtt.publish_count == 0
