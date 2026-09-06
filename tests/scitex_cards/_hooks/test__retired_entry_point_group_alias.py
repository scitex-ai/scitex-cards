#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The pre-rename hook entry-point group is still honoured, and not twice.

An entry-point group is a PUBLISHED CONTRACT held in OTHER packages'
metadata, so renaming it is a migration — alias first, then remove. That
step was skipped, and on 2026-08-17 the cost was measured: the only
registered card-event consumer in the fleet
(`scitex_agent_container._listen._card_event_delivery`) was registered
under `scitex_todo.hooks` while dispatch read `scitex_cards.hooks`, which
was empty. Nothing raised. The push rail was simply silent.

These tests pin the repair AND its hazard: reading two groups makes it
possible to dispatch one handler twice, which would turn a delivery fix
into duplicate notifications.

They call `merge_hook_entry_points` with a real object rather than
patching discovery, so what runs here is the production merge itself.
"""

from __future__ import annotations

from scitex_cards._hooks._plugins import (
    ENTRY_POINT_GROUP,
    RETIRED_ENTRY_POINT_GROUP,
    merge_hook_entry_points,
)


class _EP:
    """Stand-in for an EntryPoint: discovery reads only `.name`/`.value`."""

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _EntryPoints:
    """Real object with the `.select(group=...)` surface, hand-rolled."""

    def __init__(self, by_group: dict) -> None:
        self._by_group = by_group

    def select(self, group: str) -> list:
        return list(self._by_group.get(group, []))


def test_a_hook_registered_only_in_the_retired_group_is_still_discovered():
    # Arrange — exactly the live fleet state on 2026-08-17: the consumer sits
    # under the pre-rename group and nothing is under the new one.
    eps = _EntryPoints(
        {RETIRED_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )

    # Act
    found = merge_hook_entry_points(eps)

    # Assert — before the alias this was [], so every card event dispatched
    # to nobody while looking perfectly healthy.
    assert [ep.name for ep in found] == ["sac"]


def test_a_hook_registered_in_both_groups_is_discovered_exactly_once():
    # Arrange — a producer migrating CORRECTLY registers under both for one
    # release. Concatenating the groups would dispatch it twice, so every
    # card event would notify the operator twice.
    eps = _EntryPoints(
        {
            ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")],
            RETIRED_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")],
        }
    )

    # Act
    found = merge_hook_entry_points(eps)

    # Assert
    assert len(found) == 1


def test_distinct_hooks_in_the_two_groups_are_both_discovered():
    # Arrange — dedupe keys on identity, so two DIFFERENT handlers must both
    # survive rather than one shadowing the other.
    eps = _EntryPoints(
        {
            ENTRY_POINT_GROUP: [_EP("new-consumer", "pkg.a:deliver")],
            RETIRED_ENTRY_POINT_GROUP: [_EP("old-consumer", "pkg.b:deliver")],
        }
    )

    # Act
    found = merge_hook_entry_points(eps)

    # Assert
    assert sorted(ep.name for ep in found) == ["new-consumer", "old-consumer"]


def test_the_warning_names_the_straggler(caplog):
    # Arrange — the alias must not silently absorb the debt, or the retired
    # group never empties.
    eps = _EntryPoints(
        {RETIRED_ENTRY_POINT_GROUP: [_EP("straggler", "pkg:deliver")]}
    )

    # Act
    with caplog.at_level("WARNING"):
        merge_hook_entry_points(eps)

    # Assert
    assert "straggler" in caplog.text


def test_the_warning_names_the_replacement_group(caplog):
    # Arrange — an error that only states what happened is half-written; it
    # must say what to do about it.
    eps = _EntryPoints(
        {RETIRED_ENTRY_POINT_GROUP: [_EP("straggler", "pkg:deliver")]}
    )

    # Act
    with caplog.at_level("WARNING"):
        merge_hook_entry_points(eps)

    # Assert
    assert ENTRY_POINT_GROUP in caplog.text


def test_a_hook_in_an_unrelated_group_is_not_discovered():
    # Arrange — POSITIVE CONTROL. Without this every assertion above could
    # pass vacuously against an implementation that returned EVERY entry
    # point regardless of group, and the suite would be measuring nothing.
    # Discovery has to stay group-sensitive.
    eps = _EntryPoints(
        {"some.other.package.hooks": [_EP("unrelated", "pkg:deliver")]}
    )

    # Act
    found = merge_hook_entry_points(eps)

    # Assert
    assert found == []


# EOF
