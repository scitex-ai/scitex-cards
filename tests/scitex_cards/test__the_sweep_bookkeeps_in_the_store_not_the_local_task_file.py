#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The sweeps' bookkeeping must land in the STORE, not in a local sibling file.

MEASURED 2026-08-23 on compute-04, with BOTH ledgers live at the same minute::

    Postgres sweep_state   612 rows   nudges    newest 15:06:31Z
                                      reminders newest 2026-08-18 (frozen 5 days)
    local    cards.db      445 rows   reminders newest 15:06:50Z
                                      nudges    newest 14:42:42Z

Nineteen seconds apart. The cause was ``_sweeps`` passing ``local_store_path``
(exported as ``_resolved_store``) into ``sweep_reminders(store=…)`` and
``sweep_and_nudge(store=…)``. That value answers "which local FILE", so
``_db_sweep_state`` → ``_db_target`` → ``database_for`` mapped it to the label's
SIBLING DATABASE. Meanwhile ``_cli/_stats.py`` passes the raw argument and
reached Postgres, so nudge dedup was split across two ledgers on ONE host and a
suppression recorded by one driver could not suppress the other.

WHAT THESE TESTS CANNOT DO, stated plainly because the gap is the interesting
part: they cannot reproduce the split, which needs two concurrently-written
backends and a running notifyd; and they cannot prove the CALL SITES were
rewired, because doing that without a mock would mean running a real sweep and
delivering real notifications. They pin the MAPPING that made the old value
wrong. The call-site wiring is verified by measuring the live ledgers after
notifyd restarts — a measurement, recorded on the card, not an assertion here.

Note also that ``test_on_a_database_deployment_…`` SKIPS whenever the ambient
store is a file, which is the normal test environment. A skipped calibration
proves nothing, so the two ``database_for`` tests below carry the real weight:
they are deterministic and need no ambient database.
"""

import pytest

from scitex_cards._delivery._sweeps import _sweep_store
from pathlib import Path

from scitex_cards._paths import local_store_path
from scitex_cards._store_target import database_for, resolve_store_target


def test_the_sweep_bookkeeps_in_the_resolved_store_target():
    # Arrange
    expected = resolve_store_target(None)
    # Act
    actual = _sweep_store(None)
    # Assert
    assert actual == expected


def test_an_explicitly_named_store_is_honoured_verbatim(tmp_path):
    # Arrange
    explicit = tmp_path / "tasks.yaml"
    # Act
    actual = _sweep_store(explicit)
    # Assert
    assert actual == explicit


@pytest.mark.skipif(
    "://" not in str(resolve_store_target(None)),
    reason="ambient store is a file; both resolvers agree by construction",
)
def test_on_a_database_deployment_it_differs_from_the_local_task_file():
    # Arrange
    local_file = local_store_path(None)
    # Act
    agrees_with_local_file = _sweep_store(None) == local_file
    # Assert
    assert not agrees_with_local_file


def test_a_task_file_label_maps_to_its_local_sibling(tmp_path):
    """Why passing the LOCAL PATH was wrong: the label becomes a local file."""
    # Arrange
    label = tmp_path / "tasks.yaml"
    # Act
    mapped = Path(database_for(label))
    # Assert
    assert mapped.name == "cards.db"


def test_a_database_store_survives_the_mapping_unchanged():
    """Why passing the STORE TARGET is right: database_for is then the identity."""
    # Arrange
    dsn = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
    # Act
    mapped = database_for(_sweep_store(dsn))
    # Assert
    assert mapped == dsn
