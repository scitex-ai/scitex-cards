#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``list-stale`` help summary (pending abolition).

``_cli/_stale.py`` summarized ``list-stale`` as ``List PENDING cards ...``
after ``pending`` was abolished 2026-07-10. The code was already correct
(``list_stale_cmd`` filters on ``BACKLOG_STATUS``, i.e. ``deferred``) — only
the help prose lied, inviting the next copy-paste to reintroduce a dead
filter. The summary now names the deferred backlog; these pins keep code,
help, and the status enum aligned.

These pins render ``--help`` through ``CliRunner`` / Click only: no store,
no PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import click
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._stale import list_stale_cmd
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def _help() -> str:
    out = CliRunner().invoke(main, ["list-stale", "--help"])
    assert out.exit_code == 0, out.output
    return out.output


def test_list_stale_help_names_no_abolished_status():
    text = _help()
    assert "PENDING cards" not in text
    assert "--status pending" not in text


def test_list_stale_help_names_the_deferred_backlog():
    text = _help()
    assert "deferred-backlog" in text
    # Direct command object agrees with the routed `main` rendering.
    direct = list_stale_cmd.get_help(click.Context(list_stale_cmd))
    assert "deferred-backlog" in direct
    assert "PENDING cards" not in direct


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
