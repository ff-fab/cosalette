"""Quartz-compatible cron expression parser.

Supports 6- or 7-field Quartz cron expressions::

    second minute hour day-of-month month day-of-week [year]

Features: ``*``, ``?``, ranges (``N-M``), steps (``N/S``, ``*/S``),
lists (``N,M``), named days (``SUN``–``SAT``) and months
(``JAN``–``DEC``), last day (``L``), nearest weekday (``W``),
Nth weekday of month (``#``).

See Also:
    ADR-032 — Cron scheduling and wall-clock sleep.
    http://www.quartz-scheduler.org/documentation/quartz-2.3.0/tutorials/tutorial-lesson-06.html
"""

from cosalette._cron._fields import (
    _DOW_NAMES,
    _MONTH_NAMES,
    _parse_simple_field,
)
from cosalette._cron._schedule import CronSchedule
from cosalette._cron._special import (
    _LastDay,
    _LastDow,
    _LastWeekday,
    _NearestWeekday,
    _NthDow,
    _parse_dom_field,
    _parse_dow_field,
    _quartz_to_python_dow,
    _resolve_dom_special,
    _resolve_dow_special,
)

__all__ = [
    "CronSchedule",
]
