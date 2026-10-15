#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``sweep_reminders`` state-seam tolerance gap.

The 2026-10-14 hermetic seam (``state=box``) normalised a caller-owned box
with ``setdefault`` only: a present-but-malformed section (``None`` or a
non-dict from a default-built box) survived normalisation, so the first
``state["owners"]`` index handed ``None`` downstream and the sweep raised
into the notifyd tick — contradicting its own "Never raises into the
caller" contract. The database path never had this hole:
:func:`save_reminder_state` already reads sections through ``or {}``.

These pins prove the seam mirrors that tolerance: malformed sections
become ``{}`` in place, before the first section read.

No store, no PostgreSQL: empty task lists with explicit seams only, so
they hold in containers where the store-backed suite refuses via the
fail-LOUD guard.
"""

from __future__ import annotations

import inspect

from scitex_cards._reminders import sweep_reminders


def test_none_sections_do_not_raise_and_normalise_in_place():
    # Arrange — a default-built box whose sections are explicitly None.
    box: dict = {"owners": None, "cards": None}
    # Act — an empty sweep, which must never raise on bookkeeping.
    out = sweep_reminders(
        [],
        state=box,
        enqueue=lambda *a, **k: {"ok": True},
        resolve_key=lambda name: name,
        resolve_user=lambda name: None,
        operator="operator",
        interval_minutes=5.0,
    )
    # Assert — the documented empty result, and the box healed in place.
    assert out == {"digested": [], "escalated": [], "creator_escalated": [], "skipped": []}
    assert box == {"owners": {}, "cards": {}}


def test_missing_sections_are_created():
    # Arrange — a fresh box with neither section.
    box: dict = {}
    # Act.
    out = sweep_reminders(
        [],
        state=box,
        enqueue=lambda *a, **k: {"ok": True},
        resolve_key=lambda name: name,
        resolve_user=lambda name: None,
        operator="operator",
        interval_minutes=5.0,
    )
    # Assert — sections exist as dicts, result empty.
    assert out["digested"] == [] and out["escalated"] == []
    assert isinstance(box.get("owners"), dict)
    assert isinstance(box.get("cards"), dict)


def test_non_dict_sections_become_empty_dicts():
    # Arrange — sections of the wrong type (a list and a string).
    box: dict = {"owners": ["alice"], "cards": "stale"}
    # Act.
    out = sweep_reminders(
        [],
        state=box,
        enqueue=lambda *a, **k: {"ok": True},
        resolve_key=lambda name: name,
        resolve_user=lambda name: None,
        operator="operator",
        interval_minutes=5.0,
    )
    # Assert — coerced, never raised.
    assert out["skipped"] == []
    assert box == {"owners": {}, "cards": {}}


def test_well_formed_box_threads_unchanged():
    # Arrange — a healthy box must not be disturbed by the tolerance.
    box: dict = {"owners": {}, "cards": {}}
    # Act.
    out = sweep_reminders(
        [],
        state=box,
        enqueue=lambda *a, **k: {"ok": True},
        resolve_key=lambda name: name,
        resolve_user=lambda name: None,
        operator="operator",
        interval_minutes=5.0,
    )
    # Assert — same empty result, sections still empty dicts.
    assert out == {"digested": [], "escalated": [], "creator_escalated": [], "skipped": []}
    assert box["owners"] == {} and box["cards"] == {}


def test_docstring_names_the_malformed_section_tolerance():
    # Arrange — the live contract text.
    doc = sweep_reminders.__doc__ or ""
    # Assert — the tolerance is documented, not just coded.
    assert "malformed" in doc
    assert "never raises" in doc


def test_source_orders_normalisation_before_first_section_read():
    # Arrange — the live source.
    src = inspect.getsource(sweep_reminders)
    # Assert — the isinstance guards precede the first section index.
    assert src.index('if not isinstance(state.get("owners"), dict)') < src.index(
        'owner_state: dict[str, dict] = state["owners"]'
    )
    assert src.index('if not isinstance(state.get("cards"), dict)') < src.index(
        'card_state: dict[str, dict] = state["cards"]'
    )
