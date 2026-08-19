#!/usr/bin/env python3
"""Expiry PROPOSES cancellation. Nothing performs it — so no surface may promise it.

`_backlog_triage.is_expired` is an age predicate and `expired()` feeds a body
that gets PRINTED. No sweep, daemon or verb in this package writes
``status=cancelled``, and no JobSpec runs ``scitex-cards triage``.

For two days the MCP ``add_task`` docstring — the surface every agent in the
fleet reads before writing a card — said the opposite: a park exempts a card
"from the backlog nudge AND from auto-expiry". Any reader takes that to mean
expiry happens by itself. scitex-dev's did. So did this package's own
maintainer, who repeated the claim verbatim in a card *about mechanisms that
do not fire*. The nudge body went further and told its human reader "silence
cancels them", which is a promise of an automatic mutation that does not
exist — read by the one person whose actual complaint is that the board has
too many cards.

The horizon moved 30d -> 7d on 2026-08-19 on the operator's instruction, which
made the lie considerably more expensive: 935 cards now sit past a line that
proposes cancellation and delivers nothing.

WHAT THESE TESTS ARE, EXACTLY. The vocabulary checks are a SPELLING barrier,
not a semantic one. They catch the two constructions that actually shipped
("auto-expiry", "auto-cancel"); they do not catch a future author writing
"expires by itself" in fresh words. That is a real limit and it is stated
rather than papered over — `test_the_detector_would_have_caught_the_sentence_
that_shipped` exists so the barrier is known to fire at all, which is the
minimum a gate must prove.

`test_no_jobspec_schedules_triage` is the load-bearing one, because it pins
the FACT the prose depends on. The docstrings are true only while nothing
schedules triage. If someone schedules it, this goes red and the prose must
be revisited in the same change — the two halves are not allowed to drift
apart silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import scitex_cards

_SRC = Path(scitex_cards.__file__).parent

#: The two constructions that actually shipped and misled real readers.
FALSE_PROMISE = ("auto-expiry", "auto-cancel")

#: The sentence as it stood on the MCP surface before 2026-08-19.
SENTENCE_THAT_SHIPPED = (
    "A non-empty reason exempts the card from the backlog nudge AND from "
    "auto-expiry — a standing goal must not be auto-cancelled at the horizon "
    "for the crime of standing."
)


def _promises_automatic_expiry(text: str) -> bool:
    """True when TEXT uses one of the constructions that misled readers."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in FALSE_PROMISE)


def _docstring_of(module_filename: str, func_name: str) -> str:
    """Return FUNC_NAME's docstring, read from source (no import required)."""
    tree = ast.parse((_SRC / module_filename).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_func and node.name == func_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{func_name} not found in {module_filename}")


def _jobspec_names() -> list[str]:
    """Every ``name=`` literal passed to a ``JobSpec(...)`` call."""
    tree = ast.parse((_SRC / "_jobs_provider.py").read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "JobSpec":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                names.append(str(kw.value.value))
    return names


def _module_int(rel_path: str, name: str) -> int:
    """Read an int module-level assignment from source (no import required)."""
    tree = ast.parse((_SRC / rel_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", None) == name:
                return int(node.value.value)
    raise AssertionError(f"{name} not found in {rel_path}")


class TestTheStaleHorizonsMoveTogether:
    """`_DEFAULT_DAYS` is duplicated. Neither copy may drift alone.

    Both are 14 while the forgetting horizon is 7. That gap is a known,
    recorded disagreement awaiting an operator decision — the Django copy
    feeds the board's Archive button, so it cannot be widened unilaterally.
    What must NOT happen is one copy moving and the other staying, which
    would make the CLI and the board silently disagree about what is stale.
    """

    def test_the_cli_and_django_stale_horizons_are_equal(self):
        # Arrange
        cli = _module_int("_cli/_stale.py", "_DEFAULT_DAYS")
        # Act
        django = _module_int("_django/handlers/stale.py", "_DEFAULT_DAYS")
        # Assert — change one, change both.
        assert cli == django

    def test_the_stale_horizon_is_not_shorter_than_the_expiry_horizon(self):
        # Arrange
        from scitex_cards._backlog_triage import DEFAULT_EXPIRY_DAYS

        # Act
        cli = _module_int("_cli/_stale.py", "_DEFAULT_DAYS")
        # Assert — stale may be more generous than expiry, never stricter.
        assert cli >= DEFAULT_EXPIRY_DAYS


class TestTheDetectorItself:
    def test_the_detector_would_have_caught_the_sentence_that_shipped(self):
        # Arrange
        historical = SENTENCE_THAT_SHIPPED
        # Act
        flagged = _promises_automatic_expiry(historical)
        # Assert — a gate that cannot fail is not a gate.
        assert flagged is True

    def test_the_detector_passes_prose_that_denies_automatic_expiry(self):
        # Arrange — the corrected wording must not trip its own barrier.
        corrected = "Nothing cancels it for you; expiry is a printed proposal."
        # Act
        flagged = _promises_automatic_expiry(corrected)
        # Assert
        assert flagged is False


class TestTheSurfacesAgentsRead:
    @pytest.mark.parametrize("func_name", ["add_task", "update_task"])
    def test_the_mcp_docstring_does_not_promise_automatic_expiry(self, func_name):
        # Arrange
        doc = _docstring_of("_mcp_write.py", func_name)
        # Act
        flagged = _promises_automatic_expiry(doc)
        # Assert
        assert flagged is False

    def test_the_mcp_add_task_docstring_states_that_nothing_expires_a_card(self):
        # Arrange
        doc = _docstring_of("_mcp_write.py", "add_task")
        # Act
        says_so = "NOTHING EXPIRES A CARD BY ITSELF" in doc
        # Assert — stated positively: an empty docstring must not pass.
        assert says_so is True

    def test_the_cli_parked_help_does_not_promise_automatic_expiry(self):
        # Arrange
        source = (_SRC / "_cli" / "_update.py").read_text(encoding="utf-8")
        # Act
        flagged = _promises_automatic_expiry(source)
        # Assert
        assert flagged is False


class TestTheFactTheProseDependsOn:
    def test_no_jobspec_schedules_triage(self):
        # Arrange
        names = _jobspec_names()
        # Act
        scheduled_triage = [n for n in names if "triage" in n.lower()]
        # Assert — if this ever fires, the docstrings above must be revisited.
        assert scheduled_triage == []

    def test_the_jobspec_list_was_actually_read(self):
        # Arrange
        names = _jobspec_names()
        # Act
        found_any = len(names)
        # Assert — guards the test above from passing on an empty parse.
        assert found_any > 0
