#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A remediation hint is a CONTROL. It must clear the condition it is printed for.

REPORTED WITH MEASUREMENT by scitex-storage, 2026-07-28, on their THIRD
duplicate-dist-info of the day. The hint said to run
``pip install --force-reinstall --no-deps <dist>``. That does not clear it:
``--force-reinstall`` removes only the files listed in the RECORD of the
dist-info it replaces, so an orphaned sibling survives. Their pass-by-pass
numbers: one pass left 1 dist-info (still broken, but the recommended action had
APPEARED to succeed), a second reached 0, and only then did a plain install
produce a clean tree.

That is worse than printing no hint at all: it spends the reader's trust and
their time, and it STOPS the investigation, because the prescribed fix ran green.
Three agents were sitting on this condition at the time of the report with their
card rail refusing every call, looking healthy from outside.

A SECOND DEFECT, found while fixing the first: the ambiguous-metadata branch
named ONE directory to delete, chosen with ``sorted(names)[0]`` — a LEXICOGRAPHIC
sort over version strings. With ``0.17.9`` and ``0.17.10`` side by side that
picks ``0.17.10``, because ``"1" < "9"``. It told the reader to delete the NEWER
directory. Both versions shipped the same day, so it was reachable in practice.

SCOPE, STATED HONESTLY: these assert the SOURCE TEXT of the two hints. They do
not drive pip. Building a genuinely duplicated dist-info tree and running pip
against it is not something this suite can do hermetically, so the claim here is
narrower than "the procedure works" — it is "the procedure we print is the one
scitex-storage measured, and the one they measured as broken is not prescribed".
The measurement lives in their report; this keeps the text from drifting back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scitex_cards import _install_probe

_SOURCE = Path(_install_probe.__file__)


@pytest.fixture
def hints() -> str:
    """Hint text only, with comments stripped.

    The comments deliberately QUOTE the banned flag to explain why it is banned,
    so a naive scan would match the explanation and call it the violation — the
    same vacuous shape as a guard that reads its own docstring.
    """
    source = _SOURCE.read_text(encoding="utf-8")
    return re.sub(r"^\s*#.*$", "", source, flags=re.M)


def test_force_reinstall_is_never_prescribed(hints: str):
    """It leaves the orphan in place, so recommending it is worse than silence."""
    # Arrange
    prescribing = re.compile(r"Reinstall:.*--force-reinstall")

    # Act
    offenders = prescribing.findall(hints)

    # Assert
    assert offenders == []


def test_the_procedure_is_uninstall_until_empty(hints: str):
    """One pass clears one dist-info; the condition needs every one gone."""
    # Arrange
    needle = "while pip uninstall"

    # Act
    present = needle in hints

    # Assert
    assert present


def test_the_reader_is_told_why_one_pass_is_not_enough(hints: str):
    """Without the reason, a reader stops at the first green-looking result.

    That is precisely how this survived three occurrences: the prescribed action
    completed successfully while the condition remained.
    """
    # Arrange
    needle = "NOT enough"

    # Act
    present = needle in hints

    # Assert
    assert present


def test_no_hint_names_a_single_dist_info_to_delete(hints: str):
    """Guessing which directory is stale is what produced the sort bug.

    `sorted(names)[0]` over version strings is lexicographic, so 0.17.10 sorts
    before 0.17.9 and the hint named the NEWER directory for deletion. The fix
    is not a better sort — it is to remove them all and install once, which
    cannot pick wrong.
    """
    # Arrange
    guessing = re.compile(r"rm -rf.*sorted\(")

    # Act
    offenders = guessing.findall(hints)

    # Assert
    assert offenders == []


# EOF
