#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The root --help must name the primitive-owned PostgreSQL contract.

Asserts the CONSTRAINT, not today's sentence: no default file path, and the
one honest answer to "which database?" is present. Any rewording that keeps
the constraint keeps passing.

The constant and the render are tested separately because neither ``main.help``
nor any positional slice of it is stable across environments:

    scitex-dev ABSENT   ``main.help`` = summary + config-resolution block,
    (fallback renderer)  and the block renders BEFORE the Options section

    scitex-dev PRESENT  ``main.help`` = the summary line ONLY; the block is
    (spec renderer)      composed at format time and renders AFTER Options

What IS stable: the constant is the single source of the text, and the text
reaches a reader in both. Hence the split -- the constraint tests read the
constant, and two more prove it actually reaches someone typing --help.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._main import _STORE_RESOLUTION


@pytest.fixture
def resolution_text() -> str:
    return "\n".join(_STORE_RESOLUTION)


@pytest.fixture
def rendered_help() -> str:
    return CliRunner().invoke(main, ["--help"]).output


def test_the_config_resolution_block_advertises_no_default_file(resolution_text):
    # Arrange
    a_default_path = re.compile(r"~/\.scitex/\S*\.db\b")
    # Act
    found = a_default_path.search(resolution_text)
    # Assert
    assert found is None, f"config resolution still advertises a file: {found}"


def test_the_config_resolution_block_points_at_resolve_store(resolution_text):
    # Arrange
    expected = "resolve-store"
    # Act
    present = expected in resolution_text
    # Assert
    assert present, "config resolution must name the verb reporting the target"


def test_the_resolution_text_reaches_a_user_invoking_help(rendered_help):
    # Arrange
    a_distinctive_phrase = "SCITEX_STORE_DSN"
    # Act
    present = a_distinctive_phrase in rendered_help
    # Assert
    assert present, "the config-resolution block must reach --help, not just exist"


def test_help_names_ports_and_the_distinct_notification_transport(resolution_text):
    # Arrange
    expected = (True, True, True, True)
    # Act
    observed = (
        "55432" in resolution_text,
        "SCITEX_CARDS_NOTIFY_DSN" in resolution_text,
        "55433" in resolution_text,
        "no SQLite" in resolution_text,
    )
    # Assert
    assert observed == expected


def test_the_summary_reaches_a_user_invoking_help(rendered_help):
    # Arrange
    expected = "Shared card database"
    # Act
    present = expected in rendered_help
    # Assert
    assert present, "the summary must reach --help"


# === The constraint applies to EVERY command's help, not just the root ====
#
# THE VACUITY THIS CLOSES. The three tests above read ONE text —
# `_STORE_RESOLUTION`, the root `--help`'s config-resolution block — and the
# suite treated that as coverage of "the help names no file fallback". It is
# not: a SUBCOMMAND's option help is a separate string, and one of them
# advertised the retired file path verbatim
# (`db set-min-client-version --db "… else ~/.scitex/cards/cards.db"`) while
# this file stayed green. Measured 2026-09-17: the guard passed, the lie
# shipped, and the two only met because a card said "remove EVERY sqlite/.db
# path" and someone went looking.
#
# So the constraint is asked of every help string click can reach, walked
# through the command tree rather than listed by hand: a hand-written list of
# commands is the same defect one refactor later.

#: A file path advertised in help as a store: `…/.scitex/…<something>.db`.
#: Deliberately narrow — it targets the RETIRED store ("pass this file and you
#: have a database"), not every mention of the word "db" (a DSN example or a
#: historical note is not the lie). Widened 2026-09-17 from `~/.scitex/…` to
#: any prefix, because the option EXAMPLES named an absolute
#: `/home/agent/.scitex/cards/cards.db` — the same claim, one prefix over.
_ADVERTISED_DEFAULT_FILE = re.compile(r"\.scitex/\S*\.db\b")


def _all_help_strings(command, seen: set[str] | None = None) -> list[tuple[str, str]]:
    """Every (command-path, help-ish string) reachable from `command`."""
    seen = seen if seen is not None else set()
    out: list[tuple[str, str]] = []
    path = command.name or "scitex-cards"
    if path in seen:
        return out
    seen.add(path)
    for text in (command.help, command.short_help, command.epilog):
        if text:
            out.append((path, text))
    for param in command.params:
        for attr in ("help", "epilog"):
            text = getattr(param, attr, None)
            if isinstance(text, str) and text:
                out.append((f"{path} {param.name}", text))
    for sub in getattr(command, "commands", {}).values():
        out.extend(_all_help_strings(sub, seen))
    return out


def _advertised(text: str) -> str | None:
    """The offending path in `text`, or None when the text is honest."""
    found = _ADVERTISED_DEFAULT_FILE.search(text)
    return found.group(0) if found else None


def test_no_subcommand_help_advertises_a_default_file_path():
    """The root block is one help string among many; ask them all."""
    # Arrange
    reachable = _all_help_strings(main)
    # Act
    offenders = [(where, _advertised(text)) for where, text in reachable if _advertised(text)]
    # Assert
    assert not offenders, (
        f"these help strings still advertise a default FILE path: {offenders} — "
        "the store has one backend and no file fallback; a reader who trusts "
        "this text looks for a database that is not the one in force"
    )


# EOF
