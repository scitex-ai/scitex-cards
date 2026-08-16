#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advice about compare-and-set must name a callable that ACCEPTS it.

THE DOOR IS NOW OPEN, AND THIS FILE INVERTED RATHER THAN DIED. Its original
subject was the opposite state: PR #790 made ``update_task`` REFUSE
``expected_revision`` because the function was a whole-document read-modify-write,
and four places kept advising the refused call anyway --

    _cli/_cardsync.py:52    "the correct write path is update_task(expected_revision=N)"
    cardsync/__init__.py:27 "the compare-and-set that shipped in 0.35.0"
    cardsync/_pg.py:29      "expose it as update_task(..., expected_revision=N)"
    cardsync/_pg.py:102     "through update_task(expected_revision=...) once that verb exists"

-- one of them user-facing CLI help asserting as fact a call that raised.

WHAT CHANGED. #872 made ``update_task`` declare ``touched_ids=[task_id]`` and
``_db_mirror`` intersects the write set with it, so the write reaches exactly one
row and the per-row guard is no longer a lie. The refusal expired with its
premise. Note that #790's refusal text stated a CONCLUSION ("refused") rather
than the CONDITION it depended on, which is why it stood for six days after the
thing that invalidated it had merged.

WHY THIS FILE STILL EXISTS. Its original docstring said: "the refusal and the
recommendation are checked against the SAME two callables, so moving the CAS
again fails here rather than in somebody's terminal." Moving the CAS is exactly
what happened, and this file DID fail -- as designed. The pairing is what
matters, not which direction it points, so the assertions flip and the guarantee
holds: shipped advice and the actual signature are checked against each other.

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import scitex_cards
from scitex_cards import _db_mirror

#: Package source root, for scanning shipped text.
SRC = Path(scitex_cards.__file__).parent

#: What must no longer ship: text telling a caller the door is shut.
STALE_REFUSAL_CLAIMS = (
    "compare-and-set is NOT available on update_task",
    "expected_revision requires replace=True",  # a real message, but not advice
)


def _shipped_text() -> dict[str, str]:
    return {
        str(p.relative_to(SRC)): p.read_text(encoding="utf-8", errors="replace")
        for p in SRC.rglob("*.py")
    }


def test_the_row_level_cas_still_accepts_expected_revision():
    """Unchanged by the move: the low-level door was always open."""
    # Arrange
    write_card = _db_mirror._write_card
    # Act
    params = inspect.signature(write_card).parameters
    # Assert
    assert "expected_revision" in params


def test_update_task_now_accepts_expected_revision():
    """The inversion. This assertion was `pytest.raises(TypeError)` until #872
    removed the premise the refusal rested on."""
    # Arrange
    call = scitex_cards.update_task
    # Act
    params = inspect.signature(call).parameters
    # Assert
    assert "expected_revision" in params


def test_expected_revision_is_a_real_parameter_not_swallowed_by_kwargs():
    """`**fields` would accept ANY keyword and write it onto the card as data.
    A named parameter is what makes the guard a guard rather than a field."""
    # Arrange
    params = inspect.signature(scitex_cards.update_task).parameters
    # Act
    kind = params["expected_revision"].kind
    # Assert
    assert kind is inspect.Parameter.KEYWORD_ONLY


def test_it_stays_opt_in_by_defaulting_to_none():
    """REJECT-by-default was ruled unusable: a writer that knows nothing about
    `revision` would abort, failing fleet writes until every container is
    current (`_migrate_v6_to_v7`). `None` must emit no guard at all."""
    # Arrange
    params = inspect.signature(scitex_cards.update_task).parameters
    # Act
    default = params["expected_revision"].default
    # Assert
    assert default is None


def test_no_shipped_text_still_claims_the_door_is_shut():
    # Arrange -- scan what we ship, not what we remember writing.
    offenders = [
        name
        for name, text in _shipped_text().items()
        if any(claim in text for claim in STALE_REFUSAL_CLAIMS)
    ]
    # Act
    real = [o for o in offenders if Path(o).name != "_db_bootstrap.py"]
    # Assert
    assert not real, (
        f"these still ship text saying compare-and-set is unavailable on "
        f"update_task: {real}. It is available now -- update the advice or it "
        f"sends the next caller to a door that opens while telling them it does not."
    )


def test_the_scanner_actually_fires():
    # Arrange -- a scanner that matches nothing looks identical to a clean tree,
    # which is how four instances of the original defect survived several releases.
    planted = "note: compare-and-set is NOT available on update_task, sorry"
    # Act
    caught = any(claim in planted for claim in STALE_REFUSAL_CLAIMS)
    # Assert
    assert caught, "the scanner would not have caught the string it exists to catch"


# EOF
