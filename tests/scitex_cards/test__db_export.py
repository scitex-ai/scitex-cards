#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB → YAML export is exact by construction (ADR-0010 backup rail).

The invariant under test: yaml → `db import` → `db export` → yaml reproduces
every record SEMANTICALLY IDENTICALLY — including keys the column mapping has
never heard of, and each record's own key order — because the export reads the
verbatim ``card_json`` / ``record_json`` payloads, never the typed columns.
A DB whose payloads are missing is REFUSED, not exported stripped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._db import connect, init_schema, resolve_db_path
from scitex_cards._db_bootstrap import import_from_yaml
from scitex_cards._db_export import ExportRefused, export_doc, export_yaml
from scitex_cards._yaml import safe_dump, safe_load


@pytest.fixture()
def seeded(tmp_path: Path) -> dict:
    """A yaml store + threads sidecar with UNKNOWN keys on every section."""
    doc = {
        "tasks": [
            {
                "id": "t-one",
                "title": "first",
                "status": "deferred",
                "agent": "a",
                "exotic_unknown_field": {"nested": [1, 2]},
            },
            {"id": "t-two", "title": "second", "status": "done", "agent": "b"},
        ],
        "users": [
            {
                "id": "u_000000000001",
                "kind": "agent",
                "names": ["a"],
                "unknown_user_key": "survives",
            }
        ],
        "inboxes": {
            "u_000000000001": [
                {
                    "id": "n_000000000001",
                    "event_type": "commented",
                    "ts": "2026-07-16T00:00:00Z",
                    "seen": False,
                    "unknown_notif_key": 42,
                }
            ]
        },
    }
    threads = {
        "dm:a::b": [
            {
                "id": "m_000000000001",
                "from": "a",
                "to": "b",
                "body": "hello",
                "ts": "2026-07-16T00:00:00Z",
                "read": False,
                "unknown_msg_key": "survives",
            }
        ]
    }
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(safe_dump(doc), encoding="utf-8")
    # The sidecar contract is a top-level `threads:` mapping.
    (tmp_path / "threads.yaml").write_text(
        safe_dump({"threads": threads}), encoding="utf-8"
    )
    db = tmp_path / "cards.db"
    import_from_yaml(tasks_path=tasks_yaml, db_path=db)
    return {"doc": doc, "threads": threads, "db": db, "tmp": tmp_path}


def test_export_reproduces_the_doc_including_unknown_keys(seeded):
    # Arrange
    db = seeded["db"]

    # Act
    doc, _threads = export_doc(db)

    # Assert — semantic identity, unknown keys included.
    assert doc == seeded["doc"]


def test_export_reproduces_the_threads_sidecar_including_unknown_keys(seeded):
    # Arrange
    db = seeded["db"]

    # Act
    _doc, threads = export_doc(db)

    # Assert — semantic identity, unknown keys included.
    assert threads == seeded["threads"]


def test_export_preserves_each_records_own_key_order(seeded):
    # Arrange
    db = seeded["db"]

    # Act
    doc, _ = export_doc(db)

    # Assert — the first card's key order is the order the yaml carried.
    assert list(doc["tasks"][0].keys()) == [
        "id",
        "title",
        "status",
        "agent",
        "exotic_unknown_field",
    ]


def _export_to_files(seeded) -> dict:
    """Run `export_yaml` into a fresh dir; return the report + the two paths."""
    out = seeded["tmp"] / "export" / "tasks.yaml"
    threads_out = seeded["tmp"] / "export" / "threads.yaml"
    report = export_yaml(db_path=seeded["db"], out=out, threads_out=threads_out)
    return {"report": report, "out": out, "threads_out": threads_out}


def test_export_yaml_file_reloads_to_the_original_doc(seeded):
    # Arrange
    # Act
    exported = _export_to_files(seeded)

    # Assert
    assert safe_load(exported["out"].read_text()) == seeded["doc"]


def test_export_yaml_threads_file_reloads_to_the_original_threads(seeded):
    # Arrange
    # Act
    exported = _export_to_files(seeded)

    # Assert
    assert safe_load(exported["threads_out"].read_text()) == {
        "threads": seeded["threads"]
    }


def test_export_yaml_report_counts_every_exported_task(seeded):
    # Arrange
    # Act
    exported = _export_to_files(seeded)

    # Assert
    assert exported["report"]["tasks"] == 2


def test_export_yaml_report_counts_every_exported_message(seeded):
    # Arrange
    # Act
    exported = _export_to_files(seeded)

    # Assert
    assert exported["report"]["messages"] == 1


def test_export_refuses_rows_without_verbatim_payload(seeded):
    # Arrange — simulate a pre-v3 row: payload stripped after import.
    conn = connect(seeded["db"])
    conn.execute("UPDATE users SET record_json = NULL")
    conn.commit()
    conn.close()

    # Act
    # Assert — refusal, not a stripped export.
    with pytest.raises(ExportRefused):
        export_doc(seeded["db"])


def _export_after_acking_every_notification(seeded) -> dict:
    """Flip every notification's `seen` in the DB, then export the record."""
    conn = connect(seeded["db"])
    conn.execute("UPDATE notifications SET seen = 1")
    conn.commit()
    conn.close()
    doc, _ = export_doc(seeded["db"])
    (record,) = doc["inboxes"]["u_000000000001"]
    return record


def test_export_overlays_the_live_seen_flag_over_the_payload(seeded):
    # Arrange — the seen flag mutates in the DB after import (poll ack).
    # Act
    record = _export_after_acking_every_notification(seeded)

    # Assert — the export reflects live state, not the import-time snapshot.
    assert record["seen"] is True


def test_export_keeps_unknown_notification_keys_under_the_overlay(seeded):
    # Arrange — the seen flag mutates in the DB after import (poll ack).
    # Act
    record = _export_after_acking_every_notification(seeded)

    # Assert — overlaying live flags must not strip unknown keys.
    assert record["unknown_notif_key"] == 42


def test_resolve_db_path_still_delegates_to_the_chain(monkeypatch, tmp_path):
    """Guard: the exporter's default path rides the S4a resolution chain."""
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_DB", str(tmp_path / "x.db"))

    # Act
    resolved = resolve_db_path()

    # Assert
    assert resolved == tmp_path / "x.db"


def _export_a_store_with_one_drained_inbox(tmp_path) -> dict:
    """Round-trip a store carrying one populated and one drained ([]) inbox.

    Regression: live-store rehearsal 2026-07-16 — 2 of 56 recipients had
    drained ([]) inboxes; zero notification rows carried no key, so the
    export silently dropped those recipients.
    """
    doc = {
        "tasks": [{"id": "t", "title": "t", "status": "done"}],
        "inboxes": {
            "busy": [{"id": "n_1", "event_type": "x", "ts": "2026", "seen": True}],
            "drained": [],
        },
    }
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(safe_dump(doc), encoding="utf-8")
    db = tmp_path / "cards.db"
    import_from_yaml(tasks_path=tasks_yaml, db_path=db)
    out, _threads = export_doc(db)
    return out


def test_export_keeps_a_drained_inbox_as_an_empty_list(tmp_path):
    # Arrange — one populated inbox, one drained.
    # Act
    out = _export_a_store_with_one_drained_inbox(tmp_path)

    # Assert — the drained key is present, as an empty list.
    assert out["inboxes"]["drained"] == []


def test_export_drops_no_recipient_when_one_inbox_is_drained(tmp_path):
    # Arrange — one populated inbox, one drained.
    # Act
    out = _export_a_store_with_one_drained_inbox(tmp_path)

    # Assert — both recipients survive the round-trip.
    assert set(out["inboxes"]) == {"busy", "drained"}


# === `db snapshot` — the cadence job's one-argv backup rail ================
#
# `--refresh` = rebuild from yaml, export, commit — one command. The cadence
# job's exact invocation shape (systemd ExecStart runs a single argv — no
# shell `&&` available), so the flag must do both halves itself.

_SNAPSHOT_DOC = {"tasks": [{"id": "t", "title": "t", "status": "done"}]}


def _seed_canonical_store(tmp_path, monkeypatch):
    """Write the canonical yaml and point the env at it, like the host run."""
    store = tmp_path / "tasks.yaml"
    store.write_text(safe_dump(_SNAPSHOT_DOC), encoding="utf-8")
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    return store


def _run_snapshot_refresh(tmp_path, monkeypatch):
    """Seed a store and run `db snapshot --refresh`; return (result, snap)."""
    from click.testing import CliRunner

    from scitex_cards._cli import main

    _seed_canonical_store(tmp_path, monkeypatch)
    snap = tmp_path / "snapshots"
    result = CliRunner().invoke(
        main,
        [
            "db",
            "snapshot",
            "--refresh",
            "--db",
            str(tmp_path / "cards.db"),
            "--dir",
            str(snap),
        ],
    )
    return result, snap


def test_snapshot_refresh_exits_clean(tmp_path, monkeypatch):
    # Arrange — canonical yaml resolved via env, like the host cadence run.
    # Act
    result, _snap = _run_snapshot_refresh(tmp_path, monkeypatch)

    # Assert
    assert result.exit_code == 0, result.output


def test_snapshot_refresh_says_it_rebuilt_the_db_from_yaml(tmp_path, monkeypatch):
    # Arrange — canonical yaml resolved via env, like the host cadence run.
    # Act
    result, _snap = _run_snapshot_refresh(tmp_path, monkeypatch)

    # Assert — the import half is reported, not silent.
    assert "refreshed DB from YAML" in result.output


def test_snapshot_refresh_exports_the_store_into_the_snapshot_dir(
    tmp_path, monkeypatch
):
    # Arrange — canonical yaml resolved via env, like the host cadence run.
    # Act
    _result, snap = _run_snapshot_refresh(tmp_path, monkeypatch)

    # Assert — read the export back; it is the original doc.
    assert safe_load((snap / "tasks.yaml").read_text()) == _SNAPSHOT_DOC


def test_snapshot_refresh_commits_the_export_into_a_git_repo(tmp_path, monkeypatch):
    # Arrange — canonical yaml resolved via env, like the host cadence run.
    # Act
    _result, snap = _run_snapshot_refresh(tmp_path, monkeypatch)

    # Assert — the commit half ran; the dir is a repo.
    assert (snap / ".git").exists()


def _run_snapshot_push_to_a_bare_remote(tmp_path, monkeypatch):
    """Bootstrap a snapshot repo wired to a bare origin, then `--push` to it.

    Returns (first_result, push_result, bare).
    """
    import subprocess

    from click.testing import CliRunner

    from scitex_cards._cli import main

    _seed_canonical_store(tmp_path, monkeypatch)
    bare = tmp_path / "offsite.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    db = tmp_path / "cards.db"
    snap = tmp_path / "snapshots"
    runner = CliRunner()
    first = runner.invoke(
        main,
        ["db", "snapshot", "--refresh", "--db", str(db), "--dir", str(snap)],
    )
    subprocess.run(
        ["git", "-C", str(snap), "remote", "add", "origin", str(bare)],
        check=True,
    )
    pushed = runner.invoke(
        main,
        [
            "db",
            "snapshot",
            "--refresh",
            "--push",
            "--db",
            str(db),
            "--dir",
            str(snap),
        ],
    )
    return first, pushed, bare


def test_snapshot_push_bootstrap_run_exits_clean(tmp_path, monkeypatch):
    # Arrange — a store, a bare origin, and a snapshot dir wired to it.
    # Act
    first, _pushed, _bare = _run_snapshot_push_to_a_bare_remote(tmp_path, monkeypatch)

    # Assert — the bootstrap snapshot the push builds on succeeded.
    assert first.exit_code == 0, first.output


def test_snapshot_push_run_exits_clean(tmp_path, monkeypatch):
    # Arrange — a store, a bare origin, and a snapshot dir wired to it.
    # Act
    _first, pushed, _bare = _run_snapshot_push_to_a_bare_remote(tmp_path, monkeypatch)

    # Assert
    assert pushed.exit_code == 0, pushed.output


def test_snapshot_push_lands_the_commit_in_the_bare_remote(tmp_path, monkeypatch):
    """--push delivers the commit to origin — verified by reading the BARE
    repo back, not by exit code (the rail's job is the off-site copy)."""
    import subprocess

    # Arrange — a store, a bare origin, and a snapshot dir wired to it.
    # Act
    _first, _pushed, bare = _run_snapshot_push_to_a_bare_remote(tmp_path, monkeypatch)

    # Assert — the BARE side has the snapshot commit.
    log = subprocess.run(
        ["git", "-C", str(bare), "log", "--oneline", "-1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "snapshot:" in log.stdout


def _run_snapshot_push_without_a_remote(tmp_path, monkeypatch):
    """`--push --json` against a snapshot dir that has no origin configured."""
    from click.testing import CliRunner

    from scitex_cards._cli import main

    _seed_canonical_store(tmp_path, monkeypatch)
    return CliRunner().invoke(
        main,
        [
            "db",
            "snapshot",
            "--refresh",
            "--push",
            "--json",
            "--db",
            str(tmp_path / "cards.db"),
            "--dir",
            str(tmp_path / "snapshots"),
        ],
    )


def test_snapshot_push_without_remote_still_exits_zero(tmp_path, monkeypatch):
    """No remote yet = legitimate local-only, exit 0, said out loud."""
    # Arrange
    # Act
    result = _run_snapshot_push_without_a_remote(tmp_path, monkeypatch)

    # Assert
    assert result.exit_code == 0, result.output


def test_snapshot_push_without_remote_reports_pushed_false(tmp_path, monkeypatch):
    import json as _json

    # Arrange
    # Act
    result = _run_snapshot_push_without_a_remote(tmp_path, monkeypatch)

    # Assert — local-only is reported honestly, not claimed as a push.
    report = _json.loads(result.output.strip().splitlines()[-1])
    assert report["pushed"] is False


def test_snapshot_push_without_remote_explains_why_it_was_local_only(
    tmp_path, monkeypatch
):
    import json as _json

    # Arrange
    # Act
    result = _run_snapshot_push_without_a_remote(tmp_path, monkeypatch)

    # Assert — the reason is said out loud, not left to guesswork.
    report = _json.loads(result.output.strip().splitlines()[-1])
    assert "no remote" in report["push_detail"]
