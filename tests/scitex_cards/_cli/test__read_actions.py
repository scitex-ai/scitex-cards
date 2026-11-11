"""Canonical read actions and compatibility routes against a private store."""

import json

import pytest
from click.testing import CliRunner

from scitex_cards import _store
from scitex_cards._cli import main


@pytest.fixture
def read_cards(postgres_dsn):
    """Populate only the harness database through the real CLI."""
    runner = CliRunner()
    for card_id, status in [("read-backlog", "deferred"), ("read-finished", "done")]:
        result = runner.invoke(
            main,
            [
                "add",
                "--assignee",
                "agent:test-suite",
                "--scope",
                "read-actions",
                "--status",
                status,
                card_id,
                card_id,
            ],
        )
        assert result.exit_code == 0, result.output
    return _store.list_tasks()


def _assert_payload(action, payload):
    if action == "show-summary":
        assert (
            payload["total"],
            payload["by_status"]["done"],
            payload["by_status"]["deferred"],
        ) == (2, 1, 1)
    else:
        assert (
            [row["id"] for row in payload["drawn"]],
            payload["expired"],
            payload["decisions"],
        ) == (
            ["read-backlog"],
            [],
            ["in_progress", "blocked+blocker", "cancelled", "keep-deferred"],
        )


@pytest.mark.parametrize("action", ["show-summary", "show-triage"])
def test_canonical_read_action_returns_store_data_without_mutation(action, read_cards):
    result = CliRunner().invoke(main, [action, "--json"])
    assert result.exit_code == 0, result.output
    _assert_payload(action, json.loads(result.stdout))
    assert _store.list_tasks() == read_cards


@pytest.mark.parametrize(
    "old,new", [("summary", "show-summary"), ("triage", "show-triage")]
)
def test_read_alias_forwards_json_and_warns_on_stderr(old, new, read_cards, tmp_path):
    result = CliRunner().invoke(
        main,
        [old, "--json"],
        env={"XDG_RUNTIME_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0, result.output
    _assert_payload(new, json.loads(result.stdout))
    assert (
        "deprecated" in result.stderr
        and new in result.stderr
        and "0.58" in result.stderr
    )
    assert _store.list_tasks() == read_cards


@pytest.mark.parametrize("action", ["show-summary", "show-triage"])
def test_read_action_rejects_unknown_option(action):
    result = CliRunner().invoke(main, [action, "--not-a-real-option"])
    assert result.exit_code == 2 and "No such option" in result.output
