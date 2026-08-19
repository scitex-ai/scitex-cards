#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The pre-rename hook entry-point group is DEAD: reported, never called.

An entry-point group is a PUBLISHED CONTRACT held in OTHER packages'
metadata, so renaming it is a migration. On 2026-08-17 the fleet's only
registered card-event consumer
(`scitex_agent_container._listen._card_event_delivery`) sat under
`scitex_todo.hooks` while dispatch read `scitex_cards.hooks`, which was
empty. Nothing raised. The push rail was simply silent.

An alias was added, and REMOVED THE NEXT DAY on the operator's ruling
(2026-08-18): break it immediately, because without a hard cut from todo
to cards the half-migrated state drags on indefinitely. An alias removes
the pressure to migrate while leaving the old name load-bearing forever.

So these tests pin the hard cut AND the thing that must NOT come back
with it: the 2026-08-17 failure was SILENCE, not breakage. A straggler
now dies loudly and by name.

They call `merge_hook_entry_points` with a real object rather than
patching discovery, so what runs here is the production merge itself.
"""

from __future__ import annotations

import logging

from scitex_cards._hooks._plugins import (
    DEAD_ENTRY_POINT_GROUP,
    ENTRY_POINT_GROUP,
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


def test_a_hook_only_in_the_dead_group_is_not_called():
    # Arrange — exactly the live fleet state on 2026-08-17
    eps = _EntryPoints(
        {DEAD_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    found = merge_hook_entry_points(eps)
    # Assert — the hard cut: registration under the old name buys nothing
    assert found == []


def test_the_dead_group_is_reported_at_error_level(caplog):
    # Arrange
    eps = _EntryPoints(
        {DEAD_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    with caplog.at_level(logging.ERROR):
        merge_hook_entry_points(eps)
    # Assert — breaking hard is fine; breaking QUIETLY is the 08-17 bug
    assert caplog.records


def test_the_report_names_the_straggler(caplog):
    # Arrange
    eps = _EntryPoints(
        {DEAD_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    with caplog.at_level(logging.ERROR):
        merge_hook_entry_points(eps)
    # Assert — the fix is per-producer, so a count would not be actionable
    assert "sac" in caplog.text


def test_the_report_names_the_group_to_migrate_to(caplog):
    # Arrange
    eps = _EntryPoints(
        {DEAD_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    with caplog.at_level(logging.ERROR):
        merge_hook_entry_points(eps)
    # Assert
    assert ENTRY_POINT_GROUP in caplog.text


def test_a_hook_in_the_current_group_is_discovered():
    # Arrange — the control: the live path must be untouched
    eps = _EntryPoints(
        {ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    found = merge_hook_entry_points(eps)
    # Assert
    assert [ep.name for ep in found] == ["sac"]


def test_a_migrated_producer_registered_in_both_is_called_once():
    # Arrange — a producer mid-migration advertises the same callable twice
    handler = _EP("sac", "sac._listen:deliver")
    eps = _EntryPoints(
        {
            ENTRY_POINT_GROUP: [handler],
            DEAD_ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")],
        }
    )
    # Act
    found = merge_hook_entry_points(eps)
    # Assert — one delivery, not two
    assert [ep.name for ep in found] == ["sac"]


def test_nothing_is_reported_when_the_dead_group_is_empty(caplog):
    # Arrange — the quiet case: a fully migrated fleet must not be nagged
    eps = _EntryPoints(
        {ENTRY_POINT_GROUP: [_EP("sac", "sac._listen:deliver")]}
    )
    # Act
    with caplog.at_level(logging.ERROR):
        merge_hook_entry_points(eps)
    # Assert
    assert not caplog.records

# EOF
