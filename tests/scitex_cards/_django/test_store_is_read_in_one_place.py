#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Only ``_request_store`` may take a store out of a request.

This defect existed because the same two lines were hand-copied into two
modules — ``views.py`` and ``handlers/dm.py`` — and then only one of them was
hardened. The write half stopped trusting the query on 2026-07-28; the read
half kept trusting it for nine more days, in an adjacent function, and nothing
noticed. A written rule would have been read by whoever already knew it.

So this fails the build instead. It walks the imported ``_django`` package and
refuses any read of a ``store`` key out of ``request.GET`` or ``request.POST``
anywhere but the one module that owns the decision. ``POST`` is included even
though no handler reads one today: scitex-hub asked for "reject
store-from-query/body outright", and the cheapest moment to refuse a channel is
before it exists.

MATCHING IS ON SYNTAX, NOT ON THE WORD "store". Every docstring in this area
contains that word, including the ones explaining why it must not be read —
a text search would match its own prose and pass forever. It is also why the
positive control below matters: a static matcher that has silently stopped
matching and a tree that is genuinely clean produce the identical empty result,
so this asserts the instrument still fires on real code before trusting it to
report an absence.

KNOWN LIMIT, stated rather than implied away: an AST matcher sees the forms it
was written for. A handler that reached the same value through a name this does
not recognise would pass. It catches the copy-paste, which is how the two
copies actually arose.
"""

from __future__ import annotations

import ast
from pathlib import Path

import scitex_cards._django

#: The module allowed to read a store off a request. Everything else delegates.
_OWNER = "_request_store.py"

#: Request dicts a caller can populate. Both are forgeable; neither may name a
#: store outside the owning module.
_UNTRUSTED_SOURCES = frozenset({"GET", "POST"})

#: Key spellings that mean "the store". The Name form is what the owning module
#: itself uses, which is what makes it usable as a positive control.
_STORE_KEY_CONSTANTS = frozenset({"store"})
_STORE_KEY_NAMES = frozenset({"STORE_QUERY_PARAM"})


def _names_the_store(node: ast.AST) -> bool:
    """Is this expression the key ``"store"``, however it is spelled?"""
    if isinstance(node, ast.Constant):
        return node.value in _STORE_KEY_CONSTANTS
    if isinstance(node, ast.Name):
        return node.id in _STORE_KEY_NAMES
    return False


def _reads_untrusted_dict(node: ast.AST) -> bool:
    """Is this expression ``<something>.GET`` or ``<something>.POST``?"""
    return isinstance(node, ast.Attribute) and node.attr in _UNTRUSTED_SOURCES


def _store_reads(source: str) -> list[int]:
    """Line numbers where ``source`` takes a ``store`` key off GET/POST."""
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        # request.GET.get("store") / request.GET.getlist("store")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "getlist")
            and _reads_untrusted_dict(node.func.value)
            and node.args
            and _names_the_store(node.args[0])
        ):
            hits.append(node.lineno)
        # request.GET["store"]
        elif (
            isinstance(node, ast.Subscript)
            and _reads_untrusted_dict(node.value)
            and _names_the_store(node.slice)
        ):
            hits.append(node.lineno)
    return hits


def _layer_sources() -> list[Path]:
    """Every Python file in the imported Django layer."""
    root = Path(scitex_cards._django.__file__).resolve().parent
    return [p for p in root.rglob("*.py") if "node_modules" not in p.parts]


def test_the_owning_module_still_matches_the_detector():
    """POSITIVE CONTROL — an absence is only evidence from a live instrument.

    ``_request_store`` demonstrably reads the query, so if this ever reports
    zero the matcher has stopped matching and the guard below is measuring
    nothing while reporting success.
    """
    # Arrange
    owner = [p for p in _layer_sources() if p.name == _OWNER]

    # Act
    hits = [line for p in owner for line in _store_reads(p.read_text())]

    # Assert
    assert hits, f"{_OWNER} no longer matches the detector — the guard is dead"


def test_no_other_module_takes_a_store_off_a_request():
    """The contract: one place decides, everyone else delegates to it."""
    # Arrange
    others = [p for p in _layer_sources() if p.name != _OWNER]

    # Act
    offenders = {
        str(p): lines for p in others if (lines := _store_reads(p.read_text()))
    }

    # Assert
    assert not offenders, (
        "a store must come from scitex_cards._django._request_store "
        f"(read_store / write_store), not from a request dict: {offenders}"
    )


# EOF
