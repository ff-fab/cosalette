"""Change-detection publish strategy with optional numeric dead-band."""

from __future__ import annotations

from cosalette._strategies._base import _is_numeric, _numeric_changed, _StrategyBase


class OnChange(_StrategyBase):
    """Publish when the telemetry payload changes.

    With ``threshold=None`` (default), uses exact equality
    (``current != previous``).

    When *threshold* is a ``float``, it acts as a **global** numeric
    dead-band: a leaf field must change by more than *threshold*
    (strict ``>``) to trigger a publish.  Non-numeric fields fall
    back to ``!=``.

    When *threshold* is a ``dict[str, float]``, each key names a
    leaf field with its own dead-band.  Use **dot-notation** for
    nested fields (e.g. ``{"sensor.temp": 0.5}``).  Fields not
    listed in the dict use exact equality.

    Thresholds are applied to **leaf values only**.  Nested dicts
    are traversed recursively — ``{"sensor": {"temp": 22.5}}``
    compares ``temp`` numerically, not the intermediate ``sensor``
    dict as a whole.

    In both threshold modes, structural changes (added or removed
    keys at any nesting level) always trigger a publish, and fields
    are combined with **OR** semantics — any single leaf field
    exceeding its threshold is sufficient.

    Args:
        threshold: Optional dead-band for numeric change detection.
            ``None`` → exact equality, ``float`` → global threshold,
            ``dict[str, float]`` → per-field thresholds (dot-notation
            for nested keys).
    """

    def __init__(
        self,
        *,
        threshold: float | dict[str, float] | None = None,
    ) -> None:
        if isinstance(threshold, dict):
            for field, value in threshold.items():
                if isinstance(value, bool):
                    msg = f"Threshold for '{field}' must be a number, got bool"
                    raise TypeError(msg)
                if value < 0:
                    msg = f"Threshold for '{field}' must be non-negative, got {value}"
                    raise ValueError(msg)
        elif isinstance(threshold, bool):
            msg = "Threshold must be a number, got bool"
            raise TypeError(msg)
        elif isinstance(threshold, (int, float)) and threshold < 0:
            msg = f"Threshold must be non-negative, got {threshold}"
            raise ValueError(msg)
        self._threshold = threshold

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return ``True`` when the payload differs from the last publish.

        When a threshold is configured, numeric fields are compared
        using ``abs(current - previous) > threshold`` (strict
        inequality).  Non-numeric fields and structural changes always
        use exact equality.
        """
        if previous is None:
            return True
        if self._threshold is None:
            return current != previous
        return self._check_with_threshold(current, previous)

    # -- internals ----------------------------------------------------------

    def _check_with_threshold(
        self,
        current: dict[str, object],
        previous: dict[str, object],
    ) -> bool:
        """Compare payloads using numeric dead-band thresholds.

        Recurses into nested dicts so that thresholds apply to
        **leaf** values only.  A top-level ``{"sensor": {"temp": 22.5}}``
        compares ``temp`` numerically — the intermediate ``"sensor"``
        dict is traversed, not compared as a whole.
        """
        return self._compare_dicts(current, previous, prefix="")

    def _compare_dicts(
        self,
        current: dict[str, object],
        previous: dict[str, object],
        prefix: str,
    ) -> bool:
        """Recursively compare two dicts, returning True if any field changed."""
        # Structural change at this level → always publish
        if current.keys() != previous.keys():
            return True

        for key in current:
            cur_val = current[key]
            prev_val = previous[key]
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"

            # Both dicts → recurse into the nested structure
            if isinstance(cur_val, dict) and isinstance(prev_val, dict):
                if self._compare_dicts(cur_val, prev_val, prefix=full_key):  # ty: ignore[invalid-argument-type]
                    return True
                continue

            if self._leaf_changed(cur_val, prev_val, full_key):
                return True

        return False

    def _leaf_changed(
        self,
        cur_val: object,
        prev_val: object,
        key: str,
    ) -> bool:
        """Return True if a single leaf field has changed beyond its threshold."""
        field_threshold = self._threshold_for(key)

        if (
            field_threshold is not None
            and _is_numeric(cur_val)
            and _is_numeric(prev_val)
        ):
            # Both numeric with a threshold — use dead-band
            assert isinstance(cur_val, (int, float))  # narrowing for mypy
            assert isinstance(prev_val, (int, float))
            return _numeric_changed(cur_val, prev_val, field_threshold)

        # Non-numeric or no threshold for this field — exact equality
        return cur_val != prev_val

    def _threshold_for(self, key: str) -> float | None:
        """Look up the threshold for *key*.

        *key* is a dot-notation path (e.g. ``"sensor.temp"``) for
        nested fields.  Returns the global float, the per-field
        value, or ``None`` if the field has no threshold entry.
        """
        if isinstance(self._threshold, dict):
            return self._threshold.get(key)
        # Global float threshold
        return self._threshold

    def on_published(self) -> None:
        """No-op — ``OnChange`` is stateless."""

    def __repr__(self) -> str:
        if self._threshold is None:
            return "OnChange()"
        return f"OnChange(threshold={self._threshold!r})"
