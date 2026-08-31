#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards db` moved to `scitex-cards dev db` (operator, 2026-08-26).

Per-package database client commands standardize on ``<package> dev db``,
and the ecosystem-wide aggregate on ``scitex-dev ecosystem dev db``. The
group itself is unchanged; only where it hangs moved, so these tests are
about REACHABILITY and the Phase-W alias rather than about any verb.

WHY THE FALLBACK IS TESTED SEPARATELY, AND IS NOT AN EDGE CASE. ``_compat``
uses scitex-dev's real ``deprecated_alias`` when it is importable and an
inline doctrine-§5 implementation otherwise — and **scitex-dev is
deliberately not a runtime dependency of this package** (see pyproject:
"scitex-dev is correctly NOT a runtime dependency"). So for an ordinary
`pip install scitex-cards` the FALLBACK is the live code path, and a
development container with scitex-dev installed exercises the other one.
The alias now points at a command object on a SIBLING group, which the
fallback could not express before this change: it resolved targets with
``group.get_command``, which only ever finds commands on the group the
alias itself sits on — so ``db`` would have resolved to the alias, not to
the moved group. Testing only through ``main`` would pass here and ship a
CLI whose ``db`` verb is broken for everyone without scitex-dev.

No mocks (PA-306): the warn-once marker is isolated by handing CliRunner a
real ``XDG_RUNTIME_DIR``, which is the documented knob ``_marker_path``
reads, not a patched attribute.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._compat import _fallback_deprecated_alias


@pytest.fixture
def runner():
    """Fresh CliRunner per test."""
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path):
    """Env pinning the warn-once marker dir at a throwaway path.

    ``_marker_path`` keys the once-per-shell flag on ``XDG_RUNTIME_DIR``,
    so without this a marker left by an earlier run (or by the developer's
    own shell) would suppress the warning and the assertion would pass for
    the wrong reason.
    """
    return {"XDG_RUNTIME_DIR": str(tmp_path)}


def _sibling_group_cli() -> click.Group:
    """A root/dev/db CLI shaped exactly like the real one, aliased via the
    fallback — the arrangement the fallback previously could not express."""

    @click.group()
    def root() -> None:
        pass

    @click.group("dev")
    def dev() -> None:
        pass

    @click.group("db")
    def db() -> None:
        pass

    @db.command("get-path")
    def get_path() -> None:
        click.echo("RESOLVED-THROUGH-THE-ALIAS")

    root.add_command(dev)
    dev.add_command(db)
    _fallback_deprecated_alias(
        root, "db", target=db, target_name="dev db", remove_in="0.54"
    )
    return root


def test_dev_db_group_exposes_the_store_verbs(runner):
    # Arrange
    argv = ["dev", "db", "--help"]
    # Act
    result = runner.invoke(main, argv)
    # Assert
    assert "get-path" in result.output


def test_root_db_alias_still_reaches_the_group(runner, isolated_env):
    # Arrange
    argv = ["db", "--help"]
    # Act
    result = runner.invoke(main, argv, env=isolated_env)
    # Assert — the alias forwards `--help` to the group it points at.
    assert "get-path" in result.output


def test_root_db_alias_names_the_new_spelling(runner, isolated_env):
    # Arrange
    argv = ["db", "--help"]
    # Act
    result = runner.invoke(main, argv, env=isolated_env)
    # Assert
    assert "dev db" in result.output


def test_root_db_alias_is_hidden_from_help():
    # Arrange
    alias = main.commands["db"]
    # Act
    hidden = alias.hidden
    # Assert — a deprecated name in the help advertises the wrong spelling.
    assert hidden is True


def test_fallback_alias_forwards_to_a_sibling_group_object(runner, isolated_env):
    # Arrange
    root = _sibling_group_cli()
    # Act
    result = runner.invoke(root, ["db", "get-path"], env=isolated_env)
    # Assert
    assert "RESOLVED-THROUGH-THE-ALIAS" in result.output


# EOF
