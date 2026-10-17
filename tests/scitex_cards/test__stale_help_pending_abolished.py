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


def test_list_stale_help_names_no_abolished_status_case1():
    # Arrange
    # Act
    text = _help()
    # Assert
    assert "PENDING cards" not in text


def test_list_stale_help_names_no_abolished_status_case2():
    # Arrange
    # Act
    text = _help()
    # Assert
    assert "--status pending" not in text


def test_list_stale_help_names_the_deferred_backlog_case1():
    # Arrange
    # Act
    text = _help()
    # Assert
    direct = list_stale_cmd.get_help(click.Context(list_stale_cmd))
    assert "deferred-backlog" in text


def test_list_stale_help_names_the_deferred_backlog_case2():
    # Arrange
    # Act
    text = _help()
    direct = list_stale_cmd.get_help(click.Context(list_stale_cmd))
    # Assert
    assert "deferred-backlog" in direct


def test_list_stale_help_names_the_deferred_backlog_case3():
    # Arrange
    # Act
    text = _help()
    direct = list_stale_cmd.get_help(click.Context(list_stale_cmd))
    # Assert
    assert "PENDING cards" not in direct


def test_pending_stays_abolished_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_stays_abolished_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_pending_stays_abolished_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
