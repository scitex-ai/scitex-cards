#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the board /archive terminal-honesty fix.

``handle_archive`` (POST /archive, the HTTP twin of the CLI
``close --reason`` verb) stamped ``_log_meta.closed_at/by`` while flipping
``status`` to ``"deferred"`` — an OPEN status (operator ruling 2026-07-10:
"deferred は終了ではない"). Every archived card was therefore CLOSED and
OPEN at once, the exact population the ``validate-health``
``terminal_state_honest`` check FAILs on. The CLI twin has written
``"cancelled"`` (terminal) since that ruling, and
``project_board.ARCHIVE_STATUS`` is ``"cancelled"`` too — the handler was
the one twin left behind.

These pins hold the handler to ``cancelled`` without touching a database
(no Django test client, no store, no PostgreSQL — they run in containers
where the store-backed suite refuses via the fail-LOUD guard). The
store-backed ``test_archive_flips_status_to_cancelled`` in
``test_stale.py`` carries the round-trip side for CI-with-PG.
"""

from __future__ import annotations

import inspect


def _archive_source() -> str:
    from scitex_cards._django.handlers import stale as stale_handler

    return inspect.getsource(stale_handler.handle_archive)


def test_archive_source_writes_cancelled():
    # Arrange
    src = _archive_source()
    # Act + Assert: the status flip targets the terminal closed state.
    assert 'task["status"] = "cancelled"' in src


def test_archive_source_writes_no_open_status():
    # Arrange
    src = _archive_source()
    # Act + Assert: no second conditional write reintroduces an open
    # status beside the cancelled flip.
    assert 'task["status"] = "deferred"' not in src
    assert src.count('task["status"] =') == 1


def test_archived_status_is_terminal():
    # A [CLOSED]-stamped card must land in a terminal state, or the
    # health doctor reads it as work already done that still nags.
    # Arrange
    from scitex_cards._throughput import TERMINAL_STATUSES

    # Act + Assert
    assert "cancelled" in TERMINAL_STATUSES
    assert "deferred" not in TERMINAL_STATUSES


def test_archived_status_is_a_live_enum_value():
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

    # Act + Assert: the twin refuses nothing the store would only warn on.
    assert "cancelled" in VALID_STATUSES
    assert "cancelled" not in ABOLISHED_STATUSES


def test_board_archive_spelling_agrees():
    # ``project_board.ARCHIVE_STATUS`` is how "archive" is spelled on the
    # project board ("Sending a card here is how archive is spelled").
    # Arrange
    from scitex_cards._django.handlers import project_board

    # Act + Assert: both archive paths spell it the same terminal way.
    assert project_board.ARCHIVE_STATUS == "cancelled"


def test_cli_twin_parity():
    # The CLI ``close --reason`` verb this handler twins writes
    # ``status="cancelled"`` (see ``_cli/_close.py`` step 2, which records
    # the deferred overload and its 2026-07-10 resolution).
    # Arrange
    from pathlib import Path

    text = (
        Path(__file__).parents[4]
        / "src"
        / "scitex_cards"
        / "_cli"
        / "_close.py"
    ).read_text(encoding="utf-8")
    # Act + Assert
    assert 'status="cancelled"' in text


def test_archive_doc_names_the_terminal_state():
    # The next reader must not re-learn the deferred sentinel from this
    # handler's own docstring.
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler

    doc = stale_handler.handle_archive.__doc__ or ""
    # Act + Assert
    assert '"cancelled"' in doc
    assert "deferred" in doc  # named only as the ruled-out OPEN state
    assert "terminal_state_honest" in doc


def test_archive_prompt_copy_names_the_terminal_state():
    # The operator-facing Archive prompt in board_v3.html describes the
    # flip; after the fix it must name cancelled, not the old sentinel.
    # Arrange
    from pathlib import Path

    import scitex_cards

    text = (
        Path(scitex_cards.__file__).parent
        / "_django"
        / "templates"
        / "scitex_cards"
        / "board_v3.html"
    ).read_text(encoding="utf-8")
    # Act + Assert
    assert "flips status to cancelled (terminal)" in text
    assert "flips status to deferred" not in text


def test_archive_module_doc_names_the_terminal_state():
    # The module docstring is the first thing the next reader meets;
    # it must not still teach the deferred sentinel the handler fix
    # removed (same dead-prose class as handle_archive.__doc__).
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler

    doc = stale_handler.__doc__ or ""
    # Act + Assert
    assert "flips status to ``cancelled``" in doc
    assert "flips status to ``deferred``" not in doc


def test_runbook_names_the_terminal_state():
    # The reconciliation runbook is the operator-facing close contract;
    # after the archive fix it must name cancelled, not the old deferred
    # sentinel it taught before the 2026-07-10 ruling (same dead-prose
    # class as the handler module doc + board prompt).
    # Arrange
    from pathlib import Path

    text = (
        Path(__file__).parents[4]
        / "src"
        / "scitex_cards"
        / "_skills"
        / "scitex-cards"
        / "50_board-reconciliation-runbook.md"
    ).read_text(encoding="utf-8")
    # Act + Assert
    assert "flips status to `cancelled`" in text
    assert "flips status to `deferred`" not in text


def test_runbook_close_lens_names_the_terminal_state():
    # The post-close lens sentence must agree: a closed card hides as
    # cancelled (terminal), never as deferred (OPEN per 2026-07-10).
    # Arrange
    from pathlib import Path

    text = (
        Path(__file__).parents[4]
        / "src"
        / "scitex_cards"
        / "_skills"
        / "scitex-cards"
        / "50_board-reconciliation-runbook.md"
    ).read_text(encoding="utf-8")
    # Act + Assert
    assert "(status=cancelled" in text
    assert "(status=deferred)" not in text
