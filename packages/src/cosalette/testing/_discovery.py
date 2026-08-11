"""Runtime cross-check for generated Home Assistant discovery payloads.

Verifies that every ``state_topic`` a discovery generator produced was
actually published by the running app — the check that caught the
velux2mqtt phantom-entity bug (ADR-051) and, per Proposal F23, stays
valuable even after runtime discovery publication (ADR-059) ships, since
it also guards the enrichment hook's output.

See Also:
    ADR-007 for testing strategy decisions.
    ADR-051 for the phantom-entity failure class this check catches.
    ADR-059 for runtime discovery publication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cosalette._schema._consumer_gen import HaDiscoveryPayload
    from cosalette.testing._harness import AppHarness


def assert_discovery_topics_published(
    harness: AppHarness,
    payloads: Sequence[HaDiscoveryPayload],
) -> None:
    """Assert every discovery payload's ``state_topic`` was published at runtime.

    Cross-checks generated discovery payloads (from :class:`HaDiscoveryGenerator`
    or ``build_discovery_payloads``) against ``harness.mqtt.published`` — the set
    of topics the app actually published while running. Payloads with no
    ``state_topic`` (command-only entities) are skipped, since there is nothing
    to cross-check.

    Args:
        harness: An :class:`AppHarness` that has already run the app (e.g. via
            :meth:`AppHarness.run`), so ``harness.mqtt.published`` reflects
            runtime activity.
        payloads: Discovery payloads to check, typically the result of a
            discovery generator's ``.generate()`` call.

    Raises:
        AssertionError: If any payload's ``state_topic`` never appears among
            the topics actually published at runtime.
    """
    published_topics = {item[0] for item in harness.published()}
    state_topics = {
        st
        for payload in payloads
        if isinstance(st := payload.config.get("state_topic"), str)
    }
    missing = sorted(state_topics - published_topics)
    if missing:
        raise AssertionError(
            f"discovery state_topic(s) never published at runtime: {missing}\n"
            f"published topics: {sorted(published_topics)}"
        )
