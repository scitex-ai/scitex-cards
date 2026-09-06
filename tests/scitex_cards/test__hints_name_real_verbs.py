#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A hint must be runnable as printed. This proves the verbs in ours exist.

WHY THIS EXISTS (2026-07-22). ``sac`` found that ``enforce_ripgrep.sh`` — the
most-fired guard in the fleet container — blocks ``grep -r`` and steers agents
to two remedies that are both unavailable there, so an agent who fully complies
with the guardrail has nowhere correct to go. Applying that rule to our own
surface immediately found the same defect, in a worse place: two health hints
told the reader to run ``scitex-cards db import``, and the ``db`` group has no
``import`` subcommand. Both fire on the STORE IS BROKEN path — the moment the
reader most needs a correct next step.

The promise was worse than the name. "seed from an export" has no CLI surface
at all; recovery from a dump is a Python-level operation. A broken-store agent
was handed an unrunnable command AND a recovery route that does not exist.

WHY THE POSITIVE CONTROL BELOW IS LOAD-BEARING, and not ceremony. The obvious
version of this test — enumerate the CLI, assert every hinted verb is in it —
passes trivially if the enumeration returns nothing. That is the exact failure
this whole class is about: an instrument reporting success while measuring
nothing (``rg`` exits 0 and prints zero matches; a divergence checker skips
1,145 files and prints a confident total). So the control asserts that verbs we
KNOW exist are found, before any hint is judged. If enumeration breaks, the
control fails loudly instead of the suite going green over an unchecked claim.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import click
import pytest

from scitex_cards._cli import main

SRC = Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: Verbs that certainly exist. If ANY of these is missing, enumeration is
#: broken and every result below is meaningless.
CONTROL_VERBS = ("list-tasks", "add", "update", "health")

#: Program names our hints may print. There is exactly one shipped console
#: script; this tuple named two until the second was retired, after which both
#: entries were the same string.
_PROGRAMS = ("scitex-cards",)

#: A single clean command token: lowercase letters, digits, hyphens, starting
#: with a letter. Every real CLI verb matches (``list-tasks``, ``db``,
#: ``install-unit``, ``set-min-client-version``). The verb path stops at the
#: first word that does NOT — an option, a ``[--flag]`` / ``<id>`` / ``$VAR`` /
#: ``{...}`` placeholder, an f-string hole (``\x00``), or an inline alternation
#: (``push|done``, ``a / b``). A naive "starts with -/</$/{" test let ``[…]``
#: notation and slash-separated verb lists through as if they were verb tokens.
_CLEAN_VERB_TOKEN = re.compile(r"[a-z][a-z0-9-]*\Z")


def _all_verbs() -> set[str]:
    """Every command path the CLI actually exposes, e.g. ``{"db verify", ...}``."""

    def walk(group: click.Group, prefix: str = "") -> set[str]:
        found: set[str] = set()
        ctx = click.Context(group)
        for name in group.list_commands(ctx):
            cmd = group.get_command(ctx, name)
            full = f"{prefix}{name}"
            found.add(full)
            if isinstance(cmd, click.Group):
                found |= walk(cmd, full + " ")
        return found

    return walk(main)


def _string_constants(path: Path) -> list[str]:
    """Every string literal in ``path``, with concatenation already resolved.

    PARSED WITH ``ast``, NOT MATCHED WITH A REGEX, and that is the whole point.
    The first version of this file scanned RAW SOURCE for `` `...` `` with a
    pattern that forbade newlines. Hint bodies are adjacent string literals
    inside a parenthesised expression, so where the line happens to wrap decides
    whether a hint is visible — a factor with nothing to do with correctness.

    It shipped a live example: `scitex-cards db import` in
    ``_check_store_identity_agrees``, whose backticks straddled two concatenated
    f-strings. The guard was green while the dead verb it was written to catch
    was still printing, in the same file, on the total-write-outage path.

    ``ast`` resolves adjacent-literal concatenation before this ever sees the
    text, so wrapping cannot hide anything. f-strings arrive as ``JoinedStr``;
    their literal segments are read and the ``{...}`` holes are rendered as a
    placeholder, which is right for this purpose — a runtime-interpolated value
    is never part of a command's VERB path.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append(
                "".join(
                    part.value
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    else "\x00"  # an interpolation; never part of a verb path
                    for part in node.values
                )
            )
    return out


def _hint_bearing_files() -> list[Path]:
    """EVERY source file. Selection by any narrower rule has failed twice.

    v1 filtered on filenames containing "health"/"probe" — that excluded
    modules whose hints reach the reader through ``health()``'s own report.
    v2 filtered on the file containing a ``hint`` key — that excluded
    ``_paths.py``, whose store-refusal RuntimeError told the reader to run
    ``scitex-cards db import`` and is not a "hint" by any keyword test.

    Both filters were guesses about WHERE advice lives, and advice lives
    wherever someone writes a message. A backticked ``scitex-cards <verb>``
    anywhere in this package is a promise to the reader, so scan the package.
    The cost is a full parse of ~200 files, which is cheap and cannot drift.
    """
    return sorted(SRC.rglob("*.py"))


def _hinted_verbs() -> dict[str, list[str]]:
    """``{verb_path: [files]}`` for every backticked command in a hint-bearing file.

    Placeholder arguments (``<id>``, ``--flag``, interpolations) end the verb
    path, so only the command path is judged — this is about whether the VERB
    exists, not about argument validity.
    """
    found: dict[str, list[str]] = {}
    for path in _hint_bearing_files():
        for literal in _string_constants(path):
            # Backticks now pair WITHIN one resolved string, so a command split
            # across source lines is a single token here.
            for snippet in re.findall(r"`([^`]{4,160})`", literal):
                words = snippet.split()
                if not words or words[0] not in _PROGRAMS:
                    continue
                verb: list[str] = []
                for word in words[1:]:
                    if not _CLEAN_VERB_TOKEN.match(word):
                        break
                    verb.append(word)
                if verb:
                    found.setdefault(" ".join(verb), []).append(path.name)
    return found


def test_the_cli_enumeration_actually_works():
    """POSITIVE CONTROL. Without this, an empty enumeration passes everything."""
    # Arrange
    control = set(CONTROL_VERBS)
    # Act
    verbs = _all_verbs()
    # Assert
    missing = control - verbs
    assert len(verbs) > 20 and not missing, (
        f"CLI enumeration is broken: {len(verbs)} verbs found, missing known "
        f"verbs {sorted(missing)}. Every other assertion in this file is "
        f"vacuous if this fails."
    )


def test_we_actually_found_hints_to_check():
    """SECOND CONTROL: a regex that matches nothing would also pass silently."""
    # Arrange
    # Act
    hinted = _hinted_verbs()
    # Assert
    assert hinted, (
        "no hinted commands were extracted from the health/probe sources — "
        "either the sources moved or the extraction broke; either way this "
        "file is no longer checking anything."
    )


def test_every_verb_named_in_a_hint_exists():
    """The rule: a hint must be runnable as printed.

    A reader following a hint is usually in the failure path already. Sending
    them to a command that does not exist costs them the one thing they have
    least of at that moment — a next step they can trust.
    """
    # Arrange
    verbs = _all_verbs()
    hinted = _hinted_verbs()
    # Act
    offenders = [
        f"{verb!r} (named in {', '.join(sorted(set(files)))})"
        for verb, files in sorted(hinted.items())
        if verb not in verbs
    ]
    # Assert
    assert not offenders, (
        "health hints name CLI verbs that do not exist:\n  "
        + "\n  ".join(offenders)
        + "\n\nA hint must be runnable as printed. Fix the hint, or add the "
        "verb — do not leave the reader to discover it in a failure path."
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "backticks split across concatenated literals",
            """
            def h():
                return {"hint": (
                    "re-stamp the database against it (`scitex-cards db "
                    "import`). If the stamp is right, repoint."
                )}
            """,
        ),
        (
            "backticks split across concatenated F-strings",
            """
            def h(x):
                return {"hint": (
                    f"for {x}, run (`scitex-cards db "
                    f"import`) and retry."
                )}
            """,
        ),
        (
            "verb on one line, ordinary case",
            """
            def h():
                return {"hint": "run `scitex-cards db import` and retry."}
            """,
        ),
    ],
)
def test_the_parser_sees_a_verb_however_the_source_wraps(label, source, tmp_path):
    """PROVE THE PARSER CAN SEE THE THING. This is the test that was missing.

    The original file asserted that dead verbs are absent from the CLI, which
    never exercised the parser at all — so a parser that extracted NOTHING
    passed every assertion. That is exactly what happened: `db import` was live
    in _health.py and the suite was green.

    Each case below is a real shape from this codebase. All three must yield the
    same verb; if any returns nothing, hints in that shape are unprotected.
    """
    # Arrange
    module = tmp_path / "health_fixture.py"
    module.write_text(textwrap.dedent(source), encoding="utf-8")
    # Act
    literals = _string_constants(module)
    verbs = {m for lit in literals for m in re.findall(r"`([^`]{4,160})`", lit)}
    # Assert
    assert any("db import" in v for v in verbs), (
        f"{label}: parser did not see the verb. Extracted literals={literals!r}, "
        f"backticked={verbs!r} — a hint in this shape would ship unchecked."
    )


@pytest.mark.parametrize("dead", ["db import", "db restore", "db seed"])
def test_the_check_would_catch_a_dead_verb(dead):
    """Prove this test CAN fail — the shapes that were actually shipped.

    `db import` was live in two hints until 2026-07-22. A guard that cannot
    fail is not a guard, so assert these are genuinely absent from the CLI
    rather than trusting that the check above would have noticed.
    """
    # Arrange
    # Act
    # Assert
    assert dead not in _all_verbs(), (
        f"{dead!r} now exists — if it was added deliberately, this test's "
        f"premise is stale and the hint wording should be revisited."
    )


# EOF
