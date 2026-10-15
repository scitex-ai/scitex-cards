#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for CLI help examples (pending abolition).

``list-tasks --help`` and ``find-card --help`` suggested
``--status pending`` after ``pending`` was abolished 2026-07-10. The
``--status`` filter is free text, so the suggestion fails SILENTLY with an
empty backlog — the same dead-filter class as the PreToolUse hook fix
(``redirect_claude_tasklist_to_scitex_cards.sh`` now suggests
``--status deferred``). The examples now name ``deferred`` (the successor
default); these pins keep help text, hook script, and enum mirrors
aligned.

These pins render ``--help`` through ``CliRunner`` only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import re

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_STATUS_FLAG_RE = re.compile(r"--status\s+([A-Za-z_]+)")


def _help(verb: str) -> str:
    out = CliRunner().invoke(main, [verb, "--help"])
    assert out.exit_code == 0, out.output
    return out.output


def _suggested_statuses(help_text: str) -> list[str]:
    # Only the rendered `$ ...` example lines suggest values; the
    # `--status TEXT` option signature is not a suggestion.
    return [
        m
        for line in help_text.splitlines()
        if line.lstrip().startswith("$")
        for m in _STATUS_FLAG_RE.findall(line)
    ]


def test_list_tasks_help_suggests_no_abolished_status():
    text = _help("list-tasks")
    assert "--status pending" not in text
    for value in _suggested_statuses(text):
        assert value in VALID_STATUSES, value
        assert value not in ABOLISHED_STATUSES, value


def test_list_tasks_help_suggests_the_successor_default():
    text = _help("list-tasks")
    assert "--status deferred" in text


def test_find_card_help_suggests_no_abolished_status():
    text = _help("find-card")
    assert "--status pending" not in text
    for value in _suggested_statuses(text):
        assert value in VALID_STATUSES, value
        assert value not in ABOLISHED_STATUSES, value


def test_find_card_help_suggests_the_successor_default():
    text = _help("find-card")
    assert "--status deferred" in text


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
