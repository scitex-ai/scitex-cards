#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board's JS enums must equal the Python ones they claim to mirror.

`searchQuery.js` hard-codes `VALID_STATUSES` and `VALID_KINDS` so the search
bar can offer them as qualifiers, and its own comment says the list "Mirrors
_model.py VALID_STATUSES". Nothing enforced that sentence, and both lists had
drifted by the time anyone looked (2026-09-06):

    VALID_STATUSES  JS carried `pending`, ABOLISHED in Python on 2026-07-10
    VALID_KINDS     JS was missing `status`

Neither is cosmetic. The search bar offered `status:pending` as a valid
qualifier for a value the store REFUSES on write, so the operator got a
recognised pill and zero matches; and `kind:status` — a kind cards really
have — could not be searched for at all.

WHY THIS TEST IS IN PYTHON. There ARE JS unit tests for this module, and one
of them was already red on exactly this drift — `test__search_suggest.js`
asserting 7 statuses against a list of 8. Nobody saw it because **no workflow
runs the JS suite**: twelve files, ~200 assertions, executed by nothing
(card `gui-node-tests-not-run-by-ci-20260815`). A test that does not run is
not a weaker test, it is a decoration that reads like coverage.

So this check is written in the language CI already runs. It parses the JS
literal rather than trusting a comment, and it fails in the pytest matrix the
moment either list moves — whatever happens to the node suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._task import VALID_KINDS, VALID_STATUSES

_SEARCH_QUERY_JS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "searchQuery.js"
)


def _js_string_array(source: str, name: str) -> list[str]:
    """Return the string members of a `const <name> = [ ... ];` literal.

    Deliberately ignores comments and whitespace by extracting only quoted
    members, so a commented-out entry does not count as a member and a
    reformat does not break the test.
    """
    match = re.search(
        rf"const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*;",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} literal not found in {_SEARCH_QUERY_JS.name}"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"""["']([^"']+)["']""", body)


@pytest.fixture(scope="module")
def js_source() -> str:
    return _SEARCH_QUERY_JS.read_text(encoding="utf-8")


def test_the_js_status_enum_equals_the_python_one(js_source):
    # Arrange
    # Act
    js_statuses = _js_string_array(js_source, "VALID_STATUSES")
    # Assert
    assert sorted(js_statuses) == sorted(VALID_STATUSES), (
        "searchQuery.js VALID_STATUSES has drifted from scitex_cards._task. "
        "The board would offer a qualifier the store does not accept, or omit "
        "one that cards really carry."
    )


def test_the_js_kind_enum_equals_the_python_one(js_source):
    # Arrange
    # Act
    js_kinds = _js_string_array(js_source, "VALID_KINDS")
    # Assert
    assert sorted(js_kinds) == sorted(VALID_KINDS), (
        "searchQuery.js VALID_KINDS has drifted from scitex_cards._task."
    )


def test_an_abolished_status_is_not_offered_by_the_board(js_source):
    # The specific defect this file was written for: `pending` was abolished
    # 2026-07-10 and survived in the JS list for two months. Pinned by name so
    # a future re-introduction fails loudly rather than only shifting a count.
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    js_statuses = set(_js_string_array(js_source, "VALID_STATUSES"))
    # Assert
    assert js_statuses.isdisjoint(ABOLISHED_STATUSES), (
        f"the board offers abolished status(es) "
        f"{sorted(js_statuses & set(ABOLISHED_STATUSES))} as search qualifiers"
    )
