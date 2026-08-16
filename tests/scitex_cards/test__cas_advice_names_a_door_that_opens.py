#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advice about compare-and-set must name a callable that ACCEPTS it.

WHAT WENT WRONG, measured 2026-08-16. PR #790 correctly made ``update_task``
REFUSE ``expected_revision``: update_task is a whole-document read-modify-write,
so a per-row revision guard there would silently overwrite concurrent edits to
OTHER cards. The guard raises a TypeError naming the real path.

Four places kept telling callers to use the refused door:

    _cli/_cardsync.py:52    "the correct write path is update_task(expected_revision=N)"
    cardsync/__init__.py:27 "the compare-and-set that shipped in 0.35.0"
    cardsync/_pg.py:29      "expose it as update_task(..., expected_revision=N)"
    cardsync/_pg.py:102     "through update_task(expected_revision=...) once that verb exists"

The first is USER-FACING CLI HELP, asserting as fact a call that raises. The
second asserts it SHIPPED. The other two wait for a verb that was deliberately
ruled out, so they wait forever. Constitution s2: an error -- or an instruction
-- must name the next step, and a dead end is not one.

WHY A TEST AND NOT JUST A FIX. This drifted because the guard and the advice
live in different files and nothing tied them together, so fixing the guard left
the advice pointing at the old door. The pairing is now asserted: the refusal and
the recommendation are checked against the SAME two callables, so moving the CAS
again fails here rather than in somebody's terminal.

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards import _db_mirror

#: Package source root, for scanning shipped text.
SRC = Path(scitex_cards.__file__).parent

#: The call that must never be advised, spelled as it appears in prose.
BAD_ADVICE = "update_task(expected_revision"

#: The same, as the keyword form that also appeared.
BAD_ADVICE_KW = "update_task(..., expected_revision"


def _shipped_text() -> dict[str, str]:
    return {
        str(p.relative_to(SRC)): p.read_text(encoding="utf-8", errors="replace")
        for p in SRC.rglob("*.py")
    }


def test_the_real_cas_accepts_expected_revision():
    # Arrange -- the door the advice now points at.
    write_card = _db_mirror._write_card
    # Act
    params = inspect.signature(write_card).parameters
    # Assert
    assert "expected_revision" in params


def test_update_task_refuses_expected_revision():
    # Arrange -- the door the advice used to point at; PR #790 locked it.
    call = scitex_cards.update_task
    # Act
    kwargs = {"expected_revision": 1}
    # Assert
    with pytest.raises(TypeError):
        call(None, "probe-card-that-need-not-exist", **kwargs)


def test_the_refusal_names_the_working_path():
    # Arrange -- a refusal that does not say where to go is half-written (s2).
    try:
        scitex_cards.update_task(None, "probe-card-that-need-not-exist", expected_revision=1)
        message = ""
    except TypeError as exc:
        message = str(exc)
    # Act
    names_it = "_write_card" in message
    # Assert
    assert names_it, f"refusal does not name the real CAS path: {message!r}"


def test_no_shipped_text_advises_the_refused_call():
    # Arrange -- scan what we ship, not what we remember writing.
    offenders = [
        name
        for name, text in _shipped_text().items()
        if BAD_ADVICE in text or BAD_ADVICE_KW in text
    ]
    # Act
    allowed = {"_store_mutate.py", "tests"}
    real = [o for o in offenders if Path(o).name not in allowed]
    # Assert
    assert not real, (
        f"these ship advice to call the refused door: {real}. "
        "Point them at _db_mirror._write_card(..., expected_revision=N)."
    )


def test_the_scanner_actually_fires():
    # Arrange -- a scanner that matches nothing looks identical to a clean tree,
    # which is how four instances of this survived several releases.
    planted = "the correct write path is update_task(expected_revision=N)"
    # Act
    caught = BAD_ADVICE in planted
    # Assert
    assert caught, "the scanner would not have caught the string that shipped"


# EOF
