#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex-todo notifyd`` CLI verb (slice 2).

Uses click's ``CliRunner`` against the real root group — no mocks. Covers:
* ``notifyd --once`` runs a single real delivery pass and exits (no daemon).
* ``notifyd install-unit`` writes the unit to a tmp ``$XDG_CONFIG_HOME`` and
  prints the operator-gated enable commands WITHOUT running systemctl.
"""

from __future__ import annotations

import json
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


def _run_install_unit(tmp_path, env, monkeypatch):
    """Install the systemd unit under a tmp $XDG_CONFIG_HOME.

    Returns ``(result, target_path, subprocess_calls)``.
    """
    env.set("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list = []
    real_run = subprocess.run
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (calls.append((a, k)), real_run(*a, **k))[1],
    )
    result = CliRunner().invoke(main, ["notifyd", "install-unit"])
    target = tmp_path / "cfg" / "systemd" / "user" / "scitex-todo-notifyd.service"
    return result, target, calls


def test_notifyd_install_unit_exits_zero(tmp_path, env, monkeypatch):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env, monkeypatch)
    # Assert
    assert result.exit_code == 0, result.output


def test_notifyd_install_unit_writes_the_unit_file(tmp_path, env, monkeypatch):
    # Arrange
    # Act
    _result, target, _calls = _run_install_unit(tmp_path, env, monkeypatch)
    # Assert
    assert target.exists()


def test_notifyd_install_unit_reports_what_it_wrote(tmp_path, env, monkeypatch):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env, monkeypatch)
    # Assert
    assert "wrote systemd user unit" in result.output


def test_notifyd_install_unit_prints_the_enable_commands(tmp_path, env, monkeypatch):
    # Arrange
    # Act
    result, _target, _calls = _run_install_unit(tmp_path, env, monkeypatch)
    # Assert — the operator-gated commands are printed for them to run.
    assert "systemctl --user daemon-reload" in result.output


def test_notifyd_install_unit_never_runs_systemctl(tmp_path, env, monkeypatch):
    # Arrange
    # Act
    _result, _target, calls = _run_install_unit(tmp_path, env, monkeypatch)
    # Assert — the tool printed the commands but never SHELLED OUT.
    assert calls == []


def _sweep_with_none_store(env, monkeypatch):
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
    # SCITEX_TODO_REMINDER_OWNERS / a real config.yaml; neutralise both so this
    # owner ("alice") is nagged regardless of the host's settings.
    env.delete("SCITEX_TODO_REMINDER_OWNERS")
    monkeypatch.setattr("scitex_cards._config.config_paths", lambda: [])

    _run_reminder_sweep(store=None, now=_now_utc())  # must NOT raise

    notes = poll_inbox("alice", unseen_only=False, mark_seen=False)
    return [n for n in notes if n["event_type"] == "reminder"]


def test_run_reminder_sweep_resolves_none_store_and_enqueues(env, monkeypatch):
    # Arrange
    # Act
    digest = _sweep_with_none_store(env, monkeypatch)
    # Assert — the owner gets ONE digest (event_type "reminder").
    assert len(digest) == 1


def test_the_reminder_digest_names_the_stale_card(env, monkeypatch):
    # Arrange
    # Act
    digest = _sweep_with_none_store(env, monkeypatch)
    # Assert
    assert "c1" in digest[0]["body"]


# EOF
