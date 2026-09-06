#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex-cards notifyd`` CLI verb (slice 2).

Uses click's ``CliRunner`` against the real root group — no mocks. Covers:
* ``notifyd --once`` runs a single real delivery pass and exits (no daemon).
* ``notifyd install-unit`` writes the unit to a tmp ``$XDG_CONFIG_HOME`` and
  prints the operator-gated enable commands WITHOUT running systemctl.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from scitex_cards._cli._main import main
from scitex_cards._inbox import enqueue
from scitex_cards._paths import resolve_tasks_path


def _seed(recipient="u_cli"):
    enqueue(
        recipient,
        event_type="reassigned",
        card_id="c1",
        body="hi",
        actor="a",
        ts="2026-06-27T10:00:00Z",
    )


def _run_notifyd_once():
    """One real delivery pass over a seeded store with a log channel.

    The store is provisioned per-test by ``tests/conftest.py``; both the seed
    and the daemon resolve it the same way, so nothing here names a path.
    ``recipients.json`` is a sibling of the resolved store by contract
    (``_delivery._recipients.recipients_path``).
    """
    _seed()
    store_dir = resolve_tasks_path(None).parent
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "recipients.json").write_text(
        json.dumps({"users": {"u_cli": {"channels": [{"kind": "log"}]}}}),
        encoding="utf-8",
    )
    runner = CliRunner()
    return runner.invoke(main, ["notifyd", "--once"])


def test_notifyd_once_exits_zero():
    # Arrange
    # Act
    result = _run_notifyd_once()
    # Assert
    assert result.exit_code == 0, result.output


def test_notifyd_once_announces_the_single_pass():
    # Arrange
    # Act
    result = _run_notifyd_once()
    # Assert
    assert "notifyd --once" in result.output


def test_notifyd_once_runs_single_pass():
    # Arrange
    # Act
    result = _run_notifyd_once()
    # Assert — the seeded notification really went out.
    assert "sent=1" in result.output


def _run_install_unit(tmp_path, env):
    """Install the systemd unit under a tmp $XDG_CONFIG_HOME.

    Returns ``(result, target_path, systemctl_marker)``.

    NO subprocess patch. `install-unit` must WRITE the unit and PRINT the
    `systemctl --user` commands for the operator, never shell out itself — so
    instead of wrapping `subprocess.run` we put a REAL `systemctl` first on
    $PATH. It is an ordinary shell script that appends its argv to
    ``systemctl_marker``. If the command ever shells out, the script really
    runs and the marker really appears; the absence of that file is evidence
    from the system rather than from a recorded call list.

    This is stronger than the wrapper it replaces: the old spy only saw calls
    routed through `subprocess.run`, so an `os.system`, a `Popen`, or a
    `run` imported directly into the module would have slipped past it.
    """
    env.set("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "systemctl-invocations.log"
    shim = bin_dir / "systemctl"
    shim.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(marker) + '"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env.set("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = CliRunner().invoke(main, ["notifyd", "install-unit"])
    target = tmp_path / "cfg" / "systemd" / "user" / "scitex-cards-notifyd.service"
    return result, target, marker


def test_notifyd_install_unit_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env)
    # Assert
    assert result.exit_code == 0, result.output


def test_notifyd_install_unit_writes_the_unit_file(tmp_path, env):
    # Arrange
    # Act
    _result, target, _calls = _run_install_unit(tmp_path, env)
    # Assert
    assert target.exists()


def test_notifyd_install_unit_reports_what_it_wrote(tmp_path, env):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env)
    # Assert
    assert "wrote systemd user unit" in result.output


def test_notifyd_install_unit_prints_the_enable_commands(tmp_path, env):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env)
    # Assert — the operator-gated commands are printed for them to run.
    assert "systemctl --user daemon-reload" in result.output


def test_notifyd_install_unit_never_runs_systemctl(tmp_path, env):
    # Arrange
    # Act
    _result, _target, systemctl_log = _run_install_unit(tmp_path, env)
    # Assert — a real `systemctl` sat first on $PATH throughout. If the command
    # had shelled out, that script would have run and written this file.
    assert not systemctl_log.exists()


def _sweep_with_none_store(env, tmp_path):
    """Run the reminder sweep with ``store=None`` over one stale card.

    Regression: the notifyd tick calls ``_run_reminder_sweep(store=None)`` (the
    daemon resolves its store internally), but the sweep passed None straight
    to load_tasks → Path(None) → TypeError, so the nag never ran. It must now
    resolve None itself, load the store, and enqueue a reminder for a stale
    card — without raising. Returns the owner's digest notifications.
    """
    from scitex_cards._delivery._daemon import _run_reminder_sweep
    from scitex_cards._inbox import poll_inbox
    from scitex_cards._store import add_task
    from scitex_cards._throughput import _now_utc

    add_task(
        id="c1",
        title="x",
        status="deferred",
        agent="alice",
        last_activity="2026-01-01T00:00:00Z",
    )
    # Hermetic: a deployed container scopes the nag to one agent via
    # SCITEX_CARDS_REMINDER_OWNERS / a real config file; neutralise both so this
    # owner ("alice") is nagged regardless of the host's settings.
    #
    # The config layer is neutralised by REDIRECTING it, not by replacing
    # `config_paths` with `lambda: []`. `_user_root()` honours $SCITEX_DIR and
    # the project layer is found by walking up from the cwd for a `.git`, so
    # pointing both at empty tmp dirs makes the real resolver return real paths
    # to files that genuinely do not exist — an empty config for the same
    # reason production would see one.
    env.delete("SCITEX_CARDS_REMINDER_OWNERS")
    empty = tmp_path / "no-config"
    (empty / "cards").mkdir(parents=True, exist_ok=True)
    env.set("SCITEX_DIR", str(empty))
    norepo = tmp_path / "norepo"
    norepo.mkdir(parents=True, exist_ok=True)
    env.chdir(norepo)

    _run_reminder_sweep(store=None, now=_now_utc())  # must NOT raise

    notes = poll_inbox("alice", unseen_only=False, mark_seen=False)
    return [n for n in notes if n["event_type"] == "reminder"]


def test_run_reminder_sweep_resolves_none_store_and_enqueues(env, tmp_path):
    # Arrange
    # Act
    digest = _sweep_with_none_store(env, tmp_path)
    # Assert — the owner gets ONE digest (event_type "reminder").
    assert len(digest) == 1


def test_the_reminder_digest_names_the_stale_card(env, tmp_path):
    # Arrange
    # Act
    digest = _sweep_with_none_store(env, tmp_path)
    # Assert
    assert "c1" in digest[0]["body"]


# EOF
