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


_EXPECTED = {
    "show-summary": (2, 1, 1),
    "show-triage": (
        ["read-backlog"],
        [],
        ["in_progress", "blocked+blocker", "cancelled", "keep-deferred"],
    ),
}


def _payload_observation(action, payload):
    if action == "show-summary":
        return (
            payload["total"],
            payload["by_status"]["done"],
            payload["by_status"]["deferred"],
        )
    return (
        [row["id"] for row in payload["drawn"]],
        payload["expired"],
        payload["decisions"],
    )


@pytest.mark.parametrize("action", ["show-summary", "show-triage"])
def test_canonical_read_action_returns_store_data_without_mutation(action, read_cards):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, [action, "--json"])
    # Assert
    assert (
        result.exit_code,
        _payload_observation(action, json.loads(result.stdout)),
        _store.list_tasks(),
    ) == (0, _EXPECTED[action], read_cards)


@pytest.mark.parametrize(
    "old,new", [("summary", "show-summary"), ("triage", "show-triage")]
)
def test_read_alias_forwards_json_and_warns_on_stderr(old, new, read_cards, tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [old, "--json"],
        env={"XDG_RUNTIME_DIR": str(tmp_path)},
    )
    # Assert
    assert (
        result.exit_code,
        _payload_observation(new, json.loads(result.stdout)),
        "deprecated" in result.stderr,
        new in result.stderr,
        "0.58" in result.stderr,
        _store.list_tasks(),
    ) == (0, _EXPECTED[new], True, True, True, read_cards)


@pytest.mark.parametrize("action", ["show-summary", "show-triage"])
def test_read_action_rejects_unknown_option(action):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, [action, "--not-a-real-option"])
    # Assert
    assert (result.exit_code, "No such option" in result.output) == (2, True)
