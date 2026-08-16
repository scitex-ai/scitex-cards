#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Channel discovery over the ``scitex_cards.delivery_channels`` group.

Mirrors the shape of scitex-dev's ``discover_jobs`` federated loader
(``scitex_dev._core.discovery`` / ``scitex_dev.jobs``): discover providers
via ``importlib.metadata.entry_points``, load each, dedup by channel name,
return a DETERMINISTIC (name-sorted) result, and SKIP-AND-WARN (never
silently swallow) any provider that fails to load — fail-loud to stderr.

The entry-point group name is deliberately ``scitex_cards.delivery_channels``
— distinct from any external runtime's entry-point group — so separate
channel registries never collide. This module has ZERO external imports.

Test seam
---------
:func:`discover_channels` takes an ``extra_providers=`` parameter so tests
inject REAL fake channel instances (a recorder, a flaky raiser) WITHOUT
touching installed entry points and WITHOUT any mock. Injected channels are
deduped + ordered with discovered ones under the same first-wins policy.
"""

from __future__ import annotations

import logging
import sys
from typing import Iterable, Mapping

from ._channel import DeliveryChannel

logger = logging.getLogger(__name__)

#: Entry-point group external + built-in channel providers register under.
ENTRY_POINT_GROUP = "scitex_cards.delivery_channels"


def _iter_entry_points(group: str):
    """Yield entry points for ``group`` (Python 3.9+ compatible)."""
    from importlib.metadata import entry_points

    if sys.version_info >= (3, 10):
        return entry_points(group=group)
    eps = entry_points()
    return eps.get(group, [])


def _warn(msg: str) -> None:
    """Surface a dropped/duplicate provider to BOTH the logger and stderr.

    Fail-loud, never silent: a channel that can't load is an operational
    problem the operator must SEE — a swallowed warning would mean a user
    silently stops receiving notifications.
    """
    logger.warning("%s", msg)
    print(f"[scitex-cards delivery] WARNING: {msg}", file=sys.stderr)


def _load_entry_point_channels() -> list[tuple[str, DeliveryChannel]]:
    """Load every entry-point channel as ``(ep_name, instance)`` tuples.

    A provider that raises on load (import error, constructor blows up,
    loaded object isn't callable) is SKIPPED with a stderr warning so one
    broken package never wedges discovery of the rest.
    """
    out: list[tuple[str, DeliveryChannel]] = []
    eps = list(_iter_entry_points(ENTRY_POINT_GROUP))
    for ep in eps:
        ep_name = getattr(ep, "name", "?")
        try:
            factory = ep.load()
            instance = factory() if callable(factory) else factory
        except Exception as exc:  # noqa: BLE001 — any load fault = skip + warn
            _warn(
                f"failed to load delivery channel entry point {ep_name!r} "
                f"({type(exc).__name__}: {exc}); skipping"
            )
            continue
        out.append((ep_name, instance))
    return out


def _normalise_extra(
    extra_providers: "Mapping[str, DeliveryChannel] | Iterable[DeliveryChannel] | None",
) -> list[tuple[str, DeliveryChannel]]:
    """Turn the ``extra_providers`` arg into ``(name, instance)`` tuples.

    Accepts either a mapping (the key is informational; the channel's own
    ``name`` attribute is the dedup key) or a plain iterable of channel
    instances. ``None`` → empty list.
    """
    if not extra_providers:
        return []
    if isinstance(extra_providers, Mapping):
        items = list(extra_providers.values())
    else:
        items = list(extra_providers)
    return [(getattr(ch, "name", "?"), ch) for ch in items]


def discover_channels(
    *,
    extra_providers: (
        "Mapping[str, DeliveryChannel] | Iterable[DeliveryChannel] | None"
    ) = None,
) -> dict[str, DeliveryChannel]:
    """Return ``{channel_name: channel}`` for every discovered channel.

    Sources, in order:

    1. Entry-point channels under :data:`ENTRY_POINT_GROUP` (discovery
       order).
    2. ``extra_providers`` — the TEST injection seam (real fake channels;
       no mocks).

    De-duplication is by the channel's own ``name`` attribute, FIRST-WINS
    (entry points before extras), matching ``discover_jobs``' first-provider
    policy. A duplicate is dropped with a stderr warning. The returned dict
    iterates in DETERMINISTIC name-sorted order so callers (and tests) get a
    stable channel sequence.

    A channel whose ``name`` is missing/empty is skipped with a warning —
    an unnamed channel can't be keyed in the ledger.
    """
    candidates = _load_entry_point_channels()
    candidates.extend(_normalise_extra(extra_providers))

    by_name: dict[str, DeliveryChannel] = {}
    for origin_name, channel in candidates:
        channel_name = getattr(channel, "name", None)
        if not channel_name or not isinstance(channel_name, str):
            _warn(
                f"channel from {origin_name!r} has no usable .name "
                f"(got {channel_name!r}); skipping"
            )
            continue
        if channel_name in by_name:
            _warn(
                f"duplicate delivery channel name {channel_name!r} ignored "
                f"(first provider wins)"
            )
            continue
        by_name[channel_name] = channel

    return {name: by_name[name] for name in sorted(by_name)}


__all__ = ["ENTRY_POINT_GROUP", "discover_channels"]

# EOF
