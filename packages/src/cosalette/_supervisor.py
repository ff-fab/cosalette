"""Supervision of framework-started asyncio tasks (ADR-081).

The framework starts one asyncio task per ``@app.device``, ungrouped
``@app.telemetry`` entity, coalescing group, ``@app.periodic`` handler and
``@app.stream`` handler, plus its own internal loops.  :class:`TaskSupervisor`
attaches a done-callback to each of them so a task that dies is reported
within one event-loop turn instead of at shutdown, and applies the app-wide
``on_task_failure`` policy:

- ``"restart"`` (default) re-creates the task with exponential backoff
  (1 s doubling to a 60 s cap, no jitter) under a per-registration budget
  that resets after a quiet period; an exhausted budget escalates to exit.
- ``"exit"`` escalates on the first failure.
- ``"ignore"`` reports the failure and leaves the entity offline.

Escalation sets the shutdown event so the normal graceful teardown runs;
:meth:`App.run` then raises :class:`TaskSupervisionError`, which
:meth:`App.cli` maps to :data:`~cosalette._constants.EXIT_TASK_FAILURE`.

Framework-internal loops (heartbeat, freshness watchdog, adapter health
checker, MQTT connection and refresh loops) always escalate straight to exit.

A coalescing-group member whose ``init=`` raises is isolated to that member
(:meth:`TaskSupervisor.member_init_failed`): the group task keeps running
its other members and retries the member's ``init=`` in place under its own
budget.  An exhausted member stays offline until the process restarts; it
does not exit the app.

See Also:
    ADR-081 — Supervision of framework-started tasks.
    ADR-029 — Adapter auto-restart (owns tasks while an adapter restarts).
    ADR-077 — Multi-source availability (the ``"supervisor"`` source).
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from cosalette._clock import ClockPort
    from cosalette._errors import ErrorPublisher
    from cosalette._health import HealthReporter

logger = logging.getLogger(__name__)

TaskFailurePolicy = Literal["restart", "exit", "ignore"]
"""What the supervisor does when a framework-started task dies (ADR-081)."""

TASK_FAILURE_POLICIES: tuple[TaskFailurePolicy, ...] = ("restart", "exit", "ignore")

SUPERVISOR_SOURCE = "supervisor"
"""Availability source the supervisor marks a dead task's entities with."""

DEFAULT_TASK_MAX_RESTARTS = 3
DEFAULT_TASK_RESTART_WINDOW = 300.0
RESTART_BACKOFF_INITIAL = 1.0
RESTART_BACKOFF_CAP = 60.0
CLOSE_GRACE = 2.0
"""Seconds :meth:`TaskSupervisor.aclose` waits for in-flight failure reports."""

# Strictest wins when the members of one task resolve to different policies.
_POLICY_RANK: dict[str, int] = {"ignore": 0, "restart": 1, "exit": 2}


class TaskSupervisionError(RuntimeError):
    """A supervised task failed and the app shut down because of it (ADR-081).

    Raised by :meth:`App.run` after the graceful teardown when a task's
    restart budget is exhausted, when ``on_task_failure="exit"`` and a task
    failed, or when a framework-internal loop died.  :meth:`App.cli` maps it
    to exit code ``4``.  The original exception is chained as
    ``__cause__``.

    Attributes:
        task_name: Name of the failed task, e.g. ``"telemetry:radon"``.
        restart_count: Restarts of that task in the current budget window.
        internal: ``True`` when a framework-internal loop died.
    """

    def __init__(self, task_name: str, restart_count: int, *, internal: bool) -> None:
        self.task_name = task_name
        self.restart_count = restart_count
        self.internal = internal
        if internal:
            msg = f"Framework loop {task_name!r} died"
        else:
            msg = f"Task {task_name!r} failed after {restart_count} restart(s)"
        super().__init__(msg)


def validate_task_failure_policy(value: object) -> TaskFailurePolicy:
    """Return *value* as a policy, or raise ``ValueError`` for anything else."""
    if value not in TASK_FAILURE_POLICIES:
        allowed = ", ".join(repr(p) for p in TASK_FAILURE_POLICIES)
        msg = f"on_task_failure must be one of {allowed}, got {value!r}"
        raise ValueError(msg)
    return value  # ty: ignore[invalid-return-type]


def resolve_task_failure_policy(
    app_policy: TaskFailurePolicy,
    registrations: Sequence[object] = (),
) -> TaskFailurePolicy:
    """Return the effective ``on_task_failure`` policy for one task.

    This is the single place the policy is resolved (ADR-081 section 5).
    Today every registration inherits *app_policy*; a per-registration
    override can be added here later without a breaking change.  When the
    members of one task (a coalescing group) resolve to different policies,
    the strictest wins: ``exit`` > ``restart`` > ``ignore``.
    """
    policies = [_registration_policy(reg, app_policy) for reg in registrations]
    return max(policies or [app_policy], key=_POLICY_RANK.__getitem__)


def _registration_policy(
    registration: object, app_policy: TaskFailurePolicy
) -> TaskFailurePolicy:
    """Return one registration's policy; no per-registration override exists yet."""
    del registration
    return app_policy


def restart_backoff(restart_number: int) -> float:
    """Seconds to wait before the *restart_number*-th consecutive restart.

    1 s before the first, doubling per consecutive restart, capped at 60 s,
    with no jitter (ADR-081 section 3).
    """
    exponent = max(restart_number - 1, 0)
    # Cap the exponent too, so a huge budget cannot overflow the float.
    return min(RESTART_BACKOFF_INITIAL * 2.0 ** min(exponent, 32), RESTART_BACKOFF_CAP)


@dataclass(frozen=True, slots=True)
class TaskRestartCounters:
    """Read-only snapshot of one registration's supervision counters.

    Attributes:
        restarts_in_window: Restarts counted against the current budget;
            reset to 0 after ``task_restart_window`` seconds without a failure.
        total_restarts: Restarts since the app started.
        last_failure: Clock-port (monotonic) time of the last failure, or
            ``None`` when the registration never failed.
        last_failure_type: Exception class name of the last failure.
    """

    restarts_in_window: int = 0
    total_restarts: int = 0
    last_failure: float | None = None
    last_failure_type: str | None = None


RestartFactory = Callable[[], "asyncio.Task[None]"]


@dataclass(slots=True, eq=False)
class _Supervised:
    """Mutable supervision record for one registration (one task name).

    The record outlives the task instances: counters survive every
    re-creation, by the supervisor or by an ADR-029 adapter restart.
    """

    key: str
    entities: tuple[tuple[str, bool], ...]
    policy: TaskFailurePolicy
    internal: bool
    restart: RestartFactory | None
    availability: bool = True
    # Set for a coalescing-group member's ``init=`` record: the group task
    # key it belongs to.  Such a record has no task of its own; the group
    # runner retries the init in place (ADR-081 member isolation).
    member_of: str | None = None
    live: asyncio.Task[None] | None = None
    pending: asyncio.Task[None] | None = None
    restarts_in_window: int = 0
    total_restarts: int = 0
    last_failure: float | None = None
    last_failure_type: str | None = None


class TaskSupervisor:
    """Watches framework-started tasks and applies ``on_task_failure``.

    Args:
        policy: App-wide ``on_task_failure`` policy.
        max_restarts: Restarts allowed per registration before escalation.
        restart_window: Seconds without a failure after which a
            registration's restart count resets to 0.
        clock: Clock port used for backoff sleeps and failure timestamps.
        shutdown_event: Set on escalation; a set event also means the app
            is shutting down, so cancellations are expected.
        health_reporter: Marks a dead task's entities ``error`` / offline.
        error_publisher: Publishes the one error payload per crash.
    """

    def __init__(
        self,
        *,
        policy: TaskFailurePolicy = "restart",
        max_restarts: int = DEFAULT_TASK_MAX_RESTARTS,
        restart_window: float = DEFAULT_TASK_RESTART_WINDOW,
        clock: ClockPort,
        shutdown_event: asyncio.Event,
        health_reporter: HealthReporter | None = None,
        error_publisher: ErrorPublisher | None = None,
    ) -> None:
        self._policy = policy
        self._max_restarts = max_restarts
        self._restart_window = restart_window
        self._clock = clock
        self._shutdown_event = shutdown_event
        self._health = health_reporter
        self._errors = error_publisher
        self._records: dict[str, _Supervised] = {}
        self._by_task: dict[asyncio.Task[None], _Supervised] = {}
        # Weak, so a long-running app does not keep every finished task (and
        # its traceback) alive.
        self._expected: weakref.WeakSet[asyncio.Task[None]] = weakref.WeakSet()
        self._handled: weakref.WeakSet[asyncio.Task[None]] = weakref.WeakSet()
        self._background: set[asyncio.Task[None]] = set()
        self._adapter_owned: dict[str, int] = {}
        self._adapter_exhausted: Callable[[str], bool] | None = None
        self._closed = False
        self.fatal_error: TaskSupervisionError | None = None
        # Set by request_exit() for a shutdown that is not a task failure.
        self.exit_error: Exception | None = None

    # --- Registration -------------------------------------------------------

    def supervise(
        self,
        task: asyncio.Task[None],
        *,
        entities: Iterable[tuple[str, bool]] = (),
        registrations: Sequence[object] = (),
        restart: RestartFactory | None = None,
        availability: bool = True,
    ) -> None:
        """Supervise an entity, periodic or stream task.

        The task name (``device:<n>``, ``telemetry:<n>``, ``group:<g>``,
        ``periodic:<n>``, ``stream:<n>``) identifies the registration, so
        budgets and counters carry over to every replacement task.

        Args:
            task: The task to watch.
            entities: ``(name, is_root)`` of every entity the task serves.
            registrations: The registrations the task runs, for policy
                resolution.
            restart: Factory that creates and returns a replacement task;
                ``None`` makes ``"restart"`` behave like ``"exit"``.
            availability: ``False`` for entities without an availability
                topic (streams): a failure marks them ``error`` in the
                heartbeat only, never offline.
        """
        record = self._record(
            task.get_name(),
            entities=tuple(entities),
            policy=resolve_task_failure_policy(self._policy, registrations),
            internal=False,
            restart=restart,
        )
        record.availability = availability
        self._track(record, task)

    def supervise_internal(self, task: asyncio.Task[None] | None) -> None:
        """Supervise a framework-internal loop; its death always escalates."""
        if task is None:
            return
        record = self._record(
            task.get_name(), entities=(), policy="exit", internal=True, restart=None
        )
        self._track(record, task)

    def _record(
        self,
        key: str,
        *,
        entities: tuple[tuple[str, bool], ...],
        policy: TaskFailurePolicy,
        internal: bool,
        restart: RestartFactory | None,
    ) -> _Supervised:
        record = self._records.get(key)
        if record is None:
            record = _Supervised(
                key=key,
                entities=entities,
                policy=policy,
                internal=internal,
                restart=restart,
            )
            self._records[key] = record
        else:
            record.entities = entities
            record.policy = policy
            record.restart = restart
        return record

    def _track(self, record: _Supervised, task: asyncio.Task[None]) -> None:
        record.live = task
        self._by_task[task] = record
        task.add_done_callback(self._on_done)

    def set_adapter_exhausted_check(self, check: Callable[[str], bool] | None) -> None:
        """Install the predicate telling whether an entity's adapter is
        restart-exhausted (ADR-029); such tasks are not restarted."""
        self._adapter_exhausted = check

    # --- Expected cancellation ----------------------------------------------

    def expect_cancel(self, task: asyncio.Task[None]) -> None:
        """Record that the framework is about to cancel *task*.

        Must be called before ``task.cancel()``; the cancellation then never
        reaches the policy.
        """
        if task in self._by_task:
            self._expected.add(task)

    def was_handled(self, task: asyncio.Task[None]) -> bool:
        """Whether the supervisor already reported *task*'s failure."""
        return task in self._handled

    @property
    def stopping(self) -> bool:
        """Whether the app is shutting down (cancellations are expected)."""
        return self._closed or self._shutdown_event.is_set()

    # --- ADR-029 interaction ------------------------------------------------

    def begin_adapter_restart(self, entity_names: Iterable[str]) -> list[str]:
        """Hand the tasks serving *entity_names* to an ADR-029 adapter restart.

        Pending supervisor restarts for them are cancelled, and until
        :meth:`end_adapter_restart` their failures and cancellations are not
        counted against the task budget.  Returns the affected task names
        to pass back to :meth:`end_adapter_restart`.
        """
        names = set(entity_names)
        # A group member's init record has no task to hand over: the group
        # task that retries it is owned (and cancelled) through the group's
        # own record, and its budget simply carries over to the re-created
        # group (ADR-081 member isolation).
        keys = [
            record.key
            for record in self._records.values()
            if record.member_of is None
            and any(name in names for name, _ in record.entities)
        ]
        for key in keys:
            self._adapter_owned[key] = self._adapter_owned.get(key, 0) + 1
            record = self._records[key]
            if record.pending is not None:
                record.pending.cancel()
                record.pending = None
        return keys

    def end_adapter_restart(self, keys: Iterable[str]) -> None:
        """Return the tasks named *keys* to the supervisor."""
        for key in keys:
            count = self._adapter_owned.get(key, 0) - 1
            if count > 0:
                self._adapter_owned[key] = count
            else:
                self._adapter_owned.pop(key, None)

    # --- Coalescing-group members -------------------------------------------

    async def member_init_failed(
        self,
        group_key: str,
        member: str,
        exc: Exception,
        *,
        is_root: bool = False,
        registration: object = None,
    ) -> float | None:
        """Handle a coalescing-group member whose ``init=`` raised.

        The failure is isolated to the member: the group task keeps running
        its other members.  The member is reported like a dead task (one
        CRITICAL log, one error payload, offline under the ``"supervisor"``
        source with status ``error``) and its policy decides what follows.
        Its record is keyed ``"<group_key>/<member>"`` so its counters
        survive every re-creation of the group, including ADR-029 restarts.

        Returns:
            Seconds the group runner waits before calling the member's
            ``init=`` again (``"restart"`` with budget left), or ``None``
            when the member stays offline: ``"ignore"``, an exhausted
            budget, a restart-exhausted adapter, ``"exit"`` (which has
            escalated) or shutdown.
        """
        record = self._record(
            f"{group_key}/{member}",
            entities=((member, is_root),),
            policy=resolve_task_failure_policy(
                self._policy, () if registration is None else (registration,)
            ),
            internal=False,
            restart=None,
        )
        record.member_of = group_key
        if self.stopping:
            return None
        logger.critical(
            "Init of group member %r (task %r) failed: %s",
            member,
            group_key,
            exc or type(exc).__name__,
            exc_info=exc,
        )
        await self._report_failure(record, exc, "the init failure")
        return self._apply_member_policy(record, exc)

    def _apply_member_policy(
        self, record: _Supervised, exc: BaseException
    ) -> float | None:
        if self.stopping:
            return None
        self._note_failure(record, exc)
        if record.policy == "ignore":
            logger.warning(
                "Group member %r stays down (on_task_failure='ignore')", record.key
            )
            return None
        if record.policy == "exit":
            self._escalate(record, exc, "on_task_failure='exit'")
            return None
        if self._serves_exhausted_adapter(record):
            logger.error(
                "Group member %r is not retried: its adapter is permanently offline",
                record.key,
            )
            return None
        if record.restarts_in_window >= self._max_restarts:
            # Unlike a task, an exhausted member does not exit the app: the
            # rest of its group keeps running (the decision-8 model).
            logger.critical(
                "Group member %r exceeded its restart budget (%d restart(s) "
                "within %.0f s); it stays offline until the process restarts",
                record.key,
                record.restarts_in_window,
                self._restart_window,
            )
            return None
        # Counted now, unlike a task restart (counted in _restart_after when
        # the attempt runs): no timer task is created.  The group coroutine
        # keeps the retry in its own schedule and calls init= itself.  Only
        # the end of that coroutine drops it.  At shutdown that does not
        # matter.  When the group is re-created (supervisor or ADR-029
        # restart), the new group calls init= right away without counting
        # it, and that call stands in for the dropped retry.
        delay = self._count_restart(record)
        logger.warning(
            "Retrying init of group member %r in %.0f s (restart %d/%d)",
            record.key,
            delay,
            record.restarts_in_window,
            self._max_restarts,
        )
        return delay

    # --- Counters -----------------------------------------------------------

    def counters(self, task_name: str) -> TaskRestartCounters:
        """Return a snapshot of one registration's counters."""
        record = self._records.get(task_name)
        if record is None:
            return TaskRestartCounters()
        return TaskRestartCounters(
            restarts_in_window=record.restarts_in_window,
            total_restarts=record.total_restarts,
            last_failure=record.last_failure,
            last_failure_type=record.last_failure_type,
        )

    def all_counters(self) -> dict[str, TaskRestartCounters]:
        """Return a snapshot of every supervised registration's counters."""
        return {key: self.counters(key) for key in self._records}

    # --- Shutdown -----------------------------------------------------------

    async def aclose(self) -> None:
        """Stop supervising.

        Cancels pending restarts, then gives failure reports still in
        flight up to :data:`CLOSE_GRACE` seconds to publish before
        cancelling them.  Idempotent.
        """
        self._closed = True
        for record in self._records.values():
            if record.pending is not None:
                record.pending.cancel()
                record.pending = None
        pending = {t for t in self._background if not t.done()}
        if pending:
            _done, late = await asyncio.wait(pending, timeout=CLOSE_GRACE)
            for task in late:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._background.clear()

    # --- Failure detection --------------------------------------------------

    def _on_done(self, task: asyncio.Task[None]) -> None:
        """Done-callback: classify the end of *task* and schedule handling.

        Synchronous and stateless beyond bookkeeping, so it cannot die on
        its own; all awaiting happens in :meth:`_handle_failure`.
        """
        record = self._by_task.pop(task, None)
        if record is None:
            return
        if record.live is task:
            record.live = None
        if task in self._expected:
            self._expected.discard(task)
            return
        if self.stopping:
            return
        if task.cancelled():
            exc: BaseException = asyncio.CancelledError(
                f"task {record.key!r} was cancelled outside the framework"
            )
        else:
            raised = task.exception()
            if raised is None:
                # A normal return is never a failure (ADR-081 section 1).
                return
            exc = raised
        self._handled.add(task)
        if record.key in self._adapter_owned:
            if not task.cancelled():
                logger.warning(
                    "Task %r failed during an adapter restart; "
                    "the adapter restart owns it",
                    record.key,
                    exc_info=exc,
                )
            return
        self._spawn(self._handle_failure(record, exc), f"supervisor:{record.key}")

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _handle_failure(self, record: _Supervised, exc: BaseException) -> None:
        """Report a failure, then apply the policy (ADR-081 sections 2-4)."""
        names = [name for name, _ in record.entities]
        kind = "Framework loop" if record.internal else "Task"
        suffix = f" (entities: {', '.join(names)})" if names else ""
        logger.critical(
            "%s %r died%s: %s",
            kind,
            record.key,
            suffix,
            exc or type(exc).__name__,
            exc_info=exc,
        )
        await self._report_failure(record, exc, "the failure")
        self._apply_policy(record, exc)

    async def _report_failure(
        self, record: _Supervised, exc: BaseException, description: str
    ) -> None:
        """Publish a supervised failure and mark its entities unavailable."""
        try:
            await self._publish_error(record, exc)
            await self._mark_entities_failed(record)
        except Exception:
            logger.exception("Failed to report %s of %r", description, record.key)

    async def _publish_error(self, record: _Supervised, exc: BaseException) -> None:
        """Publish the one error payload for this crash."""
        if self._errors is None:
            return
        details: dict[str, object] = {
            "task_failure": True,
            "task": record.member_of or record.key,
        }
        if record.member_of is not None:
            details["member"] = record.entities[0][0]
            details["phase"] = "init"
        device: str | None = None
        is_root = False
        if len(record.entities) == 1:
            device, is_root = record.entities[0]
        elif record.entities:
            details["entities"] = [name for name, _ in record.entities]
        await self._errors.publish(
            exc,
            device=device,
            is_root=is_root,
            details=details,
            log_traceback=False,
        )

    async def _mark_entities_failed(self, record: _Supervised) -> None:
        if self._health is None:
            return
        if not record.availability:
            for name, _ in record.entities:
                self._health.mark_stream_failed(name)
            return
        for name, is_root in record.entities:
            await self._health.publish_device_unavailable(
                name, is_root=is_root, source=SUPERVISOR_SOURCE
            )
            # Last, so the heartbeat carries the reason rather than the
            # generic "unavailable" the availability publish records.
            self._health.set_device_status(name, "error")

    def _apply_policy(self, record: _Supervised, exc: BaseException) -> None:
        if self.stopping:
            return
        if record.key in self._adapter_owned:
            # An ADR-029 adapter restart began while this failure was being
            # reported; it re-creates the task and the failure is not counted.
            return
        self._note_failure(record, exc)

        if record.internal:
            self._escalate(record, exc, "a framework loop died")
            return
        if record.policy == "ignore":
            logger.warning("Task %r stays down (on_task_failure='ignore')", record.key)
            return
        if record.policy == "exit" or record.restart is None:
            self._escalate(record, exc, "on_task_failure='exit'")
            return
        if self._serves_exhausted_adapter(record):
            logger.error(
                "Task %r is not restarted: its adapter is permanently offline",
                record.key,
            )
            return
        if record.restarts_in_window >= self._max_restarts:
            logger.critical(
                "Task %r exceeded its restart budget (%d restart(s) within %.0f s)",
                record.key,
                record.restarts_in_window,
                self._restart_window,
            )
            self._escalate(record, exc, "restart budget exhausted")
            return
        delay = restart_backoff(record.restarts_in_window + 1)
        logger.warning(
            "Restarting task %r in %.0f s (restart %d/%d)",
            record.key,
            delay,
            # Counted when the attempt runs (_restart_after); log the number
            # this attempt will have.
            record.restarts_in_window + 1,
            self._max_restarts,
        )
        record.pending = self._spawn(
            self._restart_after(record, delay), f"supervisor-restart:{record.key}"
        )

    def _note_failure(self, record: _Supervised, exc: BaseException) -> None:
        """Stamp a failure, first resetting the budget after a quiet period."""
        now = self._clock.now()
        if (
            record.last_failure is not None
            and now - record.last_failure >= self._restart_window
        ):
            record.restarts_in_window = 0
        record.last_failure = now
        record.last_failure_type = type(exc).__name__

    @staticmethod
    def _count_restart(record: _Supervised) -> float:
        """Count one restart against the budget; return its backoff delay."""
        record.restarts_in_window += 1
        record.total_restarts += 1
        return restart_backoff(record.restarts_in_window)

    def _serves_exhausted_adapter(self, record: _Supervised) -> bool:
        check = self._adapter_exhausted
        if check is None:
            return False
        return any(check(name) for name, _ in record.entities)

    async def _restart_after(self, record: _Supervised, delay: float) -> None:
        await self._clock.sleep(delay)
        if record.pending is asyncio.current_task():
            record.pending = None
        if self.stopping or record.key in self._adapter_owned:
            return
        # Invariant: at most one live task per registration.  An ADR-029
        # restart may already have re-created it.
        if record.live is not None and not record.live.done():
            return
        if self._serves_exhausted_adapter(record) or record.restart is None:
            return
        # A restart consumes budget only once it is actually attempted.  A
        # pending restart cancelled by shutdown or an adapter restart must
        # leave the registration's allowance intact.
        self._count_restart(record)
        try:
            task = record.restart()
        except Exception as exc:
            await self._handle_failure(record, exc)
            return
        logger.info("Task %r restarted", record.key)
        self._track(record, task)

    def request_exit(self, error: Exception) -> None:
        """End the app gracefully and re-raise *error* after teardown.

        Used by framework checks that are not task failures, such as
        ``exit_after_stale`` (ADR-083).  The first such error wins; a task
        failure (:attr:`fatal_error`) still takes precedence when both occur.
        """
        if self.exit_error is None:
            self.exit_error = error
        logger.critical("Shutting down: %s", error)
        self._shutdown_event.set()

    def _escalate(self, record: _Supervised, exc: BaseException, reason: str) -> None:
        if self.fatal_error is None:
            error = TaskSupervisionError(
                record.key, record.restarts_in_window, internal=record.internal
            )
            error.__cause__ = exc
            self.fatal_error = error
        logger.critical("Shutting down: %s (task %r)", reason, record.key)
        self._shutdown_event.set()
