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
import subprocess

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

    A REAL ``systemctl`` is planted FIRST on ``$PATH``: a shell script that
    appends its arguments to a marker file. Nothing is patched — if the
    installer shells out to systemctl by name, the marker appears on disk, and
    the test reads the filesystem rather than a recording of a rebound
    ``subprocess.run``.

    This is stricter than the spy it replaces in the way that matters and
    looser in a way that does not. Looser: it sees systemctl invoked BY NAME,
    not every subprocess. Stricter: it observes the actual resolution the
    installer would perform, so it would still catch a call made through
    ``os.system``, ``Popen``, or any path that never touches
    ``subprocess.run`` — all of which the spy was blind to.

    Returns ``(result, target_path, systemctl_marker)``.
    """
    env.set("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "systemctl-was-invoked"
    sentinel = fake_bin / "systemctl"
    sentinel.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{marker}"\n', encoding="utf-8"
    )
    sentinel.chmod(0o755)
    env.set("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

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
    _result, _target, marker = _run_install_unit(tmp_path, env)
    # Assert — the tool printed the commands but never SHELLED OUT. The
    # marker is written by a REAL systemctl sitting first on $PATH, so its
    # absence is a fact about what the installer did, not about what a
    # rebound `subprocess.run` recorded.
    assert not marker.exists()


def test_the_planted_systemctl_would_have_been_found(tmp_path, env):
    """The control: prove the sentinel is reachable and does record.

    An absence-assertion is only worth anything if the thing whose absence is
    asserted COULD have appeared. Without this, a sentinel that was never
    executable, never on $PATH, or never wrote its marker would report the
    installer as well-behaved no matter what it did.
    """
    # Arrange
    _result, _target, marker = _run_install_unit(tmp_path, env)

    # Act — invoke it the way a shell-out would, by bare name.
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

    # Assert
    assert marker.exists()


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
    # SCITEX_CARDS_REMINDER_OWNERS / a real config.yaml; neutralise both so this
    # owner ("alice") is nagged regardless of the host's settings.
    env.delete("SCITEX_CARDS_REMINDER_OWNERS")
    # ...and neutralise the config file FOR REAL rather than stubbing
    # discovery: SCITEX_DIR names a directory with no config.json, and cwd
    # moves out of any git repo so no project config is appended either.
    # `_read_one` is fail-soft, so real discovery over an empty dir yields the
    # empty config a stub used to fake — and, unlike the stub, a NEW config
    # source added later is actually picked up here instead of staying
    # invisible to this suite. The store is unaffected: conftest pins it via
    # SCITEX_CARDS_DB, which SCITEX_DIR does not override.
    empty_root = tmp_path / "no-config"
    empty_root.mkdir(parents=True, exist_ok=True)
    env.set("SCITEX_DIR", str(empty_root))
    env.chdir(empty_root)

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
