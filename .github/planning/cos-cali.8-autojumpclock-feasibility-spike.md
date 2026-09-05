# cos-cali.8 — AutoJumpClock feasibility spike (under pytest-asyncio)

| Field       | Value                                                                  |
| ----------- | ---------------------------------------------------------------------- |
| Status      | Complete — **recommendation: NO-GO** (documented dead end)             |
| Task        | cos-cali.8 (chore, spike)                                              |
| Epic        | cos-cali — Deterministic test clock for tick/throttle timing           |
| Scope       | Timeboxed feasibility only. **No production code ships from this bead.** |
| Constraint  | No private-API access, no custom event-loop policy                      |

---

## 1. The question

Proposal B (`tmp/cosalette-manual-clock-proposal.md`, §2 — a local, intentionally
uncommitted planning artifact) asks for an `AutoJumpClock`: same gating waiter set as the shipped
`ManualClock`, but instead of the test calling `advance(seconds)`, the clock
**jumps `now()` to the earliest pending deadline whenever the event loop goes
idle**. Cited prior art: anyio's `autojump_clock`.

The ergonomic appeal is real — most suites that use `FakeClock` today for speed
would keep passing unchanged, while the timing-sensitive ones would gain a real
gate "for free", with no explicit `advance()` calls.

The feasibility question is narrow and single-pointed:

> Can "the event loop has gone idle" be detected under `pytest-asyncio`
> (`asyncio_mode = "auto"`) **without** reaching into a private API and
> **without** installing a custom event loop or loop policy?

Everything else about `AutoJumpClock` is already solved — the waiter set,
per-sleeper deadlines, and deadline-ordered release all exist in the shipped
`ManualClock`. Auto-jump is *only* those pieces plus an idle trigger. So the
spike reduces entirely to whether that trigger can be built within the stated
constraints.

## 2. Why anyio can do it and we cannot

anyio's `autojump_clock` works because **anyio owns the scheduler**. Its test
runner drives the loop, and the autojump clock is wired into anyio's own
scheduling internals: when anyio's scheduler observes that every task is
blocked, it advances its `TestClock` to the next deadline before the loop would
otherwise wait. The "all tasks blocked" signal is a first-class fact inside
anyio's own loop implementation.

Under `pytest-asyncio` with `asyncio_mode = "auto"`, tests run on a **stdlib
`asyncio` event loop** that this project does not own or subclass. The relevant
asymmetry:

| Capability anyio relies on                     | Public equivalent on a stdlib asyncio loop |
| ---------------------------------------------- | ------------------------------------------- |
| "All tasks are blocked right now" callback     | **None**                                    |
| Hook fired before the loop sleeps on a timer   | **None**                                    |
| Inspect the ready queue to see pending work    | Only `loop._ready` — **private**            |
| Own the clock the selector blocks against      | Only by installing a **custom loop**        |

`asyncio.Runner` (3.11+, what pytest-asyncio uses under the hood) exposes
`run()`, `close()`, and `get_loop()` — **no idle callback**. `BaseEventLoop`
exposes `call_soon`, `call_later`, `time()` — none of which report quiescence.
There is no public "the loop is about to block" event.

## 3. The three candidate approaches, all rejected

The proposal itself enumerates the quiescence approaches in preference order.
Evaluated against the spike constraints:

### (a) Poll `loop._ready` until it stops changing

**Rejected — violates the no-private-API constraint.** `_ready` is a
`collections.deque` internal to `BaseEventLoop`; `_scheduled` (the timer heap)
is likewise private. Both are undocumented and have changed representation
across CPython versions (and are absent entirely on alternative loops such as
`uvloop`, which the project's runtime may use in production even if tests do
not). This is exactly the option the proposal rates "brittle across Python
versions — not recommended", and the option ADR-071's Decision **explicitly
forbids** ("Do NOT inspect the loop's private `_ready` queue").

### (b) Install a custom event loop / loop policy that fires on idle

**Rejected — outside the spike's scope, and a poor fit besides.** Subclassing
`BaseEventLoop` (or its selector) to detect "`_ready` empty, only timer handles
remain" and jump time there is the only *robust* way to get a true idle signal.
But it requires overriding loop internals and installing the loop under
pytest-asyncio — which the bead scopes out ("without a custom event loop
policy"), and which cuts against ADR-007's framework-maintained,
minimal-machinery testing posture. It would also have to co-exist with whatever
loop pytest-asyncio and any downstream conftest already install, creating a
policy-ownership conflict the project should not take on for a test double.

### (c) Heuristic "pump" task that jumps when the loop *looks* idle

**Rejected — it reintroduces the exact unreliability `ManualClock` was designed
to make explicit.** This is the only approach that stays within the constraints:
a background task loops `settle()`-style (yield, observe the pending-task and
waiter sets, repeat) and calls the internal `advance` when it thinks the loop is
quiescent. But "looks idle" is precisely the heuristic the shipped `settle()`
already implements and openly documents as defeasible:

> `_STABLE_ROUNDS = 3` … *N* plain `await` hops defeat any *N*, which is why
> `settle(until=...)` exists for tests needing a guarantee rather than a margin.
> — [`packages/src/cosalette/testing/_clock.py`](../../packages/src/cosalette/testing/_clock.py)

An auto-jumping clock built on that heuristic would jump time **too early**
whenever a task takes more plain `await` hops between waking and registering its
next sleep than the stable-round margin allows. Jumping too early is not a
cosmetic bug here: it releases a waiter the test meant to keep gated, which
**silently destroys the one guarantee** — provable tick *absence* — that the
whole `cos-cali` epic exists to deliver. A gate that opens itself on a heuristic
is not a gate.

The `settle(until=predicate)` escape hatch cannot save an *automatic* clock: the
whole point of auto-jump is that the test does **not** name the effect it is
waiting for, so there is no predicate to supply.

## 4. Recommendation — NO-GO

**Do not ship `AutoJumpClock`.** No implementation satisfies all three of:

1. detects loop idle **reliably** (so it never opens the gate early),
2. uses **no private API** (`_ready` / `_scheduled` are out), and
3. installs **no custom loop or policy**.

The robust option (b) is scoped out and architecturally unwelcome; the two
in-scope options (a, c) are respectively private-API-brittle and heuristically
unsound in the one way that matters most for this epic. anyio gets auto-jump only
because it owns its scheduler, and adopting anyio's runner is a far larger
decision than a test clock warrants (and is not on the table).

### What ships instead — and why it is enough

`ManualClock` with an explicit `advance(seconds)` is the right model for this
codebase, not a fallback:

- **Explicit advance is a feature, not a tax.** A timing proof that names the
  time it moves to is more legible and more honest than one that relies on an
  invisible auto-jump. The `cos-cali.6` suite
  ([`packages/tests/unit/scheduling/test_clock_gated_timing_proofs.py`](../../packages/tests/unit/scheduling/test_clock_gated_timing_proofs.py))
  demonstrates that the explicit form reads cleanly and asserts exact counts.
- **The gate is unconditional.** Nothing but `advance()` releases a waiter, so
  tick *absence* is a hard fact, never a heuristic that a busy loop can defeat.
- **`settle(until=...)` covers the "wait for an effect" ergonomics** that
  auto-jump was partly reaching for, without surrendering the gate.

### If auto-jump ergonomics are ever demanded again

The only supported path — should a future need justify the cost — is an
**opt-in convenience wrapper**, not a new clock double: a helper that pumps
`ManualClock.advance()` bounded by a caller-supplied `settle(until=...)`
predicate, so the heuristic never runs unsupervised and the test still names its
own stopping condition. That is a thin ergonomic layer over the shipped gate,
carries no new private-API or loop-ownership risk, and is explicitly **out of
scope here** — recorded only so the dead end does not get re-walked.

## 5. Downstream note (for the cosalette-apps reply, cos-cali.9)

The reporter's route (b) fallback and their `AutoJumpClock` request can both be
answered the same way: `ManualClock` + explicit `advance()` + `settle(until=)`
covers every use case they cited, and the auto-jump variant is declined on the
feasibility grounds above rather than on preference. `AutoJumpClock` stays
descoped; this spike is the record of why.
