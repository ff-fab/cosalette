"""Discovery mixin for the App class (F23 / ADR-059 — runtime HA discovery)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cosalette._registration import validate_mqtt_name
from cosalette._wiring._discovery import DiscoveryConfig

if TYPE_CHECKING:
    from cosalette._schema._consumer_gen import HaDiscoveryPayload, HaEnrichHook


class _DiscoveryMixin:
    """Adds :meth:`discovery` — opt-in runtime HA MQTT discovery (F23)."""

    _discovery: DiscoveryConfig | None = None
    _discovery_payloads_cache: (
        tuple[DiscoveryConfig, list[HaDiscoveryPayload]] | None
    ) = None

    def discovery(
        self,
        *,
        discovery_prefix: str = "homeassistant",
        enrich: HaEnrichHook | None = None,
    ) -> None:
        """Opt into runtime Home Assistant MQTT discovery publication.

        When called, the framework publishes retained HA discovery ``config``
        payloads on the first successful MQTT connect, generated from the
        app's live, post-configure/expand registry (:meth:`asyncapi`) — so
        settings-derived (ADR-023 callable ``name=``) entity names are always
        the real, expanded ones. This dissolves the ADR-051 phantom-entity
        class entirely for HA discovery: the callable has already run by the
        time a payload's topic is constructed, so a topic nothing publishes
        to can no longer be emitted.

        Orphaned discovery topics for entities removed from config since the
        last run are cleared the same way ADR-048 already clears
        ``state``/``availability`` topics — provided a :class:`Store` is
        configured and retained-topic cleanup is active for this app (see
        :attr:`has_dynamic_entities`).

        openHAB has no equivalent runtime discovery protocol (its ``.things``/
        ``.items`` output is static configuration, not retained MQTT topics),
        so this method covers Home Assistant only; ``cosalette schema
        openhab`` remains the offline path for openHAB.

        Args:
            discovery_prefix: HA discovery topic root. Defaults to
                ``"homeassistant"``, matching Home Assistant's own default
                and the ``cosalette schema ha-discovery --prefix`` default.
            enrich: Optional callback invoked once per emitted entity,
                immediately before its discovery payload is built, as
                ``enrich(channel, prop, config)``. Mutates ``config`` in
                place; the return value is ignored. ``prop`` is ``None`` for
                a composite entity (ADR-057), which has no single backing
                property. Runs after every schema-derived merge (``extra``
                passthrough, consumer/HA overrides), so it always has the
                final word — the escape hatch for whatever the schema cannot
                express.

        Raises:
            ValueError: If *discovery_prefix* contains ``/``, ``+``, ``#``,
                or control characters.

        See Also:
            ADR-059 — Runtime HA discovery publication with enrichment hook.
            ADR-048 — Orphaned retained-topic cleanup.
            ADR-051 — Settings-aware schema pipeline (the deferred static-side
            analogue this makes unnecessary for HA discovery specifically).

        Example::

            app = cosalette.App(name="velux2mqtt", version="0.1.0")
            app.discovery()

            # Or with an enrichment hook:
            def _enrich(channel, prop, config):
                if config.get("device_class") == "cover":
                    config["device_class"] = "shutter"

            app.discovery(enrich=_enrich)
        """
        validate_mqtt_name(discovery_prefix)
        self._discovery = DiscoveryConfig(
            discovery_prefix=discovery_prefix, enrich=enrich
        )
