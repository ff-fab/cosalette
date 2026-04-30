"""Cron field parsing helpers: names, bounds, step, and simple fields."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Named lookups
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# Quartz: 1=SUN … 7=SAT
_DOW_NAMES: dict[str, int] = {
    "SUN": 1,
    "MON": 2,
    "TUE": 3,
    "WED": 4,
    "THU": 5,
    "FRI": 6,
    "SAT": 7,
}

# ---------------------------------------------------------------------------
# Field regex patterns
# ---------------------------------------------------------------------------

_HASH_RE = re.compile(r"^(\d+|[A-Z]{3})#(\d+)$", re.IGNORECASE)
_L_DOW_RE = re.compile(r"^(\d+|[A-Z]{3})L$", re.IGNORECASE)
_L_OFFSET_RE = re.compile(r"^L-(\d+)$", re.IGNORECASE)
_W_RE = re.compile(r"^(\d+)W$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Field parsing helpers
# ---------------------------------------------------------------------------


def _replace_names(token: str, names: dict[str, int]) -> str:
    """Replace named tokens (JAN, MON, …) with their numeric equivalents."""
    upper = token.upper()
    for name, val in names.items():
        upper = upper.replace(name, str(val))
    return upper


def _validate_bounds(value: int, lo: int, hi: int, token: str) -> None:
    """Raise if *value* is outside ``[lo, hi]``."""
    if value < lo or value > hi:
        msg = f"Value {value} out of range [{lo}-{hi}] in {token!r}"
        raise ValueError(msg)


def _parse_step_field(token: str, lo: int, hi: int) -> set[int]:
    """Parse a step expression like ``N/S``, ``*/S``, or ``N-M/S``."""
    base_str, step_str = token.split("/", 1)
    step = int(step_str)
    if step <= 0:
        msg = f"Step must be positive, got {step}"
        raise ValueError(msg)
    if base_str == "*":
        start = lo
    elif "-" in base_str:
        rng = base_str.split("-", 1)
        start = int(rng[0])
        end = int(rng[1])
        _validate_bounds(start, lo, hi, token)
        _validate_bounds(end, lo, hi, token)
        hi = end  # override upper bound for range/step
    else:
        start = int(base_str)
        _validate_bounds(start, lo, hi, token)
    return set(range(start, hi + 1, step))


def _parse_simple_field(
    token: str,
    lo: int,
    hi: int,
    names: dict[str, int] | None = None,
) -> set[int]:
    """Parse a simple cron field token into a set of matching values.

    Handles: ``*``, ``?``, ``N``, ``N-M``, ``N/S``, ``*/S``, ``N-M/S``,
    ``N,M,…``, and named values.
    """
    if names:
        token = _replace_names(token, names)

    if token in ("*", "?"):
        return set(range(lo, hi + 1))

    # list (must come before range/step to split correctly)
    if "," in token:
        result: set[int] = set()
        for part in token.split(","):
            result |= _parse_simple_field(part, lo, hi)
        return result

    if "/" in token:
        return _parse_step_field(token, lo, hi)

    # range
    if "-" in token:
        parts = token.split("-", 1)
        a, b = int(parts[0]), int(parts[1])
        if a > b:
            msg = f"Invalid range {a}-{b}"
            raise ValueError(msg)
        _validate_bounds(a, lo, hi, token)
        _validate_bounds(b, lo, hi, token)
        return set(range(a, b + 1))

    # single value
    val = int(token)
    _validate_bounds(val, lo, hi, token)
    return {val}
