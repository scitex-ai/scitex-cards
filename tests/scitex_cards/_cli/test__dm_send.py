#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""First-class ``scitex-cards dm send`` contract (real PostgreSQL, no mocks)."""

from __future__ import annotations

import ast
import inspect
import json
import uuid

from click.testing import CliRunner

import scitex_cards
from scitex_cards import _dm_exchange
from scitex_cards._cli import _dm as subject
from scitex_cards._cli import main
from scitex_cards._dm.read import messages_in
from scitex_cards._inbox_postgres import poll_inbox
from scitex_cards._threads import thread_key


def _invoke(*args: str):
    if "--help" not in args and "--client-request-id" not in args:
        args = (*args, "--client-request-id", f"req_test_{uuid.uuid4().hex}")
    return CliRunner().invoke(main, ["dm", "send", *args])


def test_help_registers_the_send_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dm", "--help"])
    # Assert
    assert result.exit_code == 0 and "send" in result.output


def test_send_help_names_identity_and_delivery_boundary():
    # Arrange
    help_flag = "--help"
    # Act
    result = _invoke(help_flag)
    words = " ".join(result.output.split())
    # Assert
    assert "--sender" in words and "live visibility" in words


def test_json_success_includes_the_durable_message_id():
    # Arrange
    args = ("agent:recipient", "hello", "--json")
    # Act
    result = _invoke(*args)
    payload = json.loads(result.output)
    # Assert
    assert result.exit_code == 0 and payload["message_id"].startswith("m_")


def test_public_api_is_the_responder_that_issues_the_exchange():
    # Arrange
    recipient = "agent:recipient"
    # Act
    record = scitex_cards.dm_send(
        recipient, "public contract", client_request_id="req_public_contract"
    )
    # Assert
    assert (
        record["id"].startswith("m_"),
        record["exchange_id"].startswith("xch_"),
        record["status"]["code"],
    ) == (True, True, 202)


def test_scope_spelling_is_canonicalized_to_the_live_agent_identity():
    # Arrange
    request_id = f"req_canonical_{uuid.uuid4().hex}"
    # Act
    record = scitex_cards.dm_send(
        "agent:recipient",
        "one durable address",
        sender="agent:sender",
        client_request_id=request_id,
    )
    notifications = poll_inbox("recipient", unseen_only=False)
    # Assert
    assert (
        record["from"],
        record["to"],
        any(row.get("msg_id") == record["id"] for row in notifications),
    ) == ("sender", "recipient", True)


def test_json_success_does_not_claim_delivery_or_ack():
    # Arrange
    args = ("agent:recipient", "hello", "--json")
    # Act
    result = _invoke(*args)
    payload = json.loads(result.output)
    # Assert
    assert payload["status"]["code"] == 202 and "NOT ESTABLISHED" in payload[
        "status"
    ]["message"]


def test_default_sender_is_persisted_in_postgres():
    # Arrange
    key = thread_key("test-suite", "recipient")
    # Act
    result = _invoke("agent:recipient", "from the environment", "--json")
    payload = json.loads(result.output)
    stored = messages_in(key)
    # Assert
    assert [row["id"] for row in stored] == [payload["message_id"]]


def test_sender_flag_overrides_the_environment_identity():
    # Arrange
    sender = "agent:explicit"
    # Act
    result = _invoke(
        "agent:recipient", "explicit sender", "--sender", sender, "--json"
    )
    payload = json.loads(result.output)
    stored = messages_in(thread_key("explicit", "recipient"))
    # Assert
    assert any(row["id"] == payload["message_id"] for row in stored)


def test_human_success_says_persisted_but_unconfirmed():
    # Arrange
    message = "human output"
    # Act
    result = _invoke("agent:recipient", message)
    # Assert
    assert (
        "http/202 exchange=xch_" in result.output
        and "NOT ESTABLISHED" in result.output
    )


def test_unresolved_sender_json_is_actionable(env):
    # Arrange
    env.delete("SCITEX_CARDS_AGENT_ID")
    # Act
    result = _invoke("agent:recipient", "nobody", "--json")
    payload = json.loads(result.output)
    # Assert
    assert result.exit_code == 1 and "SCITEX_CARDS_AGENT_ID" in payload["status"][
        "message"
    ]


def test_unresolved_sender_uses_native_http_status(env):
    # Arrange
    env.delete("SCITEX_CARDS_AGENT_ID")
    # Act
    payload = json.loads(_invoke("agent:recipient", "nobody", "--json").output)
    # Assert
    assert (
        payload["status"]["kind"] == "http"
        and payload["status"]["code"] == 400
        and "No DM was persisted" in payload["status"]["message"]
    )


def test_invalid_shared_ledger_target_fails_before_persisting(env):
    # Arrange
    env.set("SCITEX_STORE_DSN", "not-a-postgres-dsn")
    key = thread_key("test-suite", "recipient")
    # Act
    result = _invoke("agent:recipient", "must not land", "--json")
    payload = json.loads(result.output)
    stored = messages_in(key)
    # Assert
    assert (
        result.exit_code,
        stored,
        payload["status"]["code"],
        "No DM was persisted" in payload["status"]["message"],
        "SCITEX_STORE_DSN" in payload["status"]["message"],
    ) == (1, [], 503, True, True)


def test_unknown_error_never_echoes_a_possible_secret():
    # Arrange
    error = RuntimeError("postgresql://user:secret@host/db")
    # Act
    status = _dm_exchange.failure_status(error, _dm_exchange.new_exchange()[0])
    rendered = json.dumps(status.to_dict())
    # Assert
    assert "secret" not in rendered and "NOT ESTABLISHED" in rendered


def test_status_wire_does_not_reinvent_ok_or_retryable():
    # Arrange
    exchange_id, _opened_at = _dm_exchange.new_exchange()
    # Act
    status = _dm_exchange.failure_status(RuntimeError("boom"), exchange_id)
    # Assert
    assert set(status.to_dict()) == {"kind", "code", "message"}


def test_exchange_status_round_trips_through_shared_ledger():
    # Arrange
    sent = json.loads(_invoke("agent:recipient", "tracked", "--json").output)
    # Act
    result = CliRunner().invoke(
        main, ["dm", "get-status", sent["exchange_id"], "--json"]
    )
    payload = json.loads(result.output)
    # Assert
    assert (
        payload["status"]["code"],
        payload["final"],
        payload["initiator"],
        payload["responder"],
        payload["operation"],
    ) == (202, False, "test-suite", "recipient", "cards.dm.delivery")


def test_same_responder_exchange_id_reaches_notification_row():
    # Arrange
    sent = json.loads(_invoke("agent:recipient", "one exchange", "--json").output)
    # Act
    notifications = poll_inbox("recipient", unseen_only=False)
    matching = [n for n in notifications if n.get("msg_id") == sent["message_id"]]
    # Assert
    assert (len(matching), matching[0]["exchange_id"]) == (
        1,
        sent["exchange_id"],
    )


def test_delivery_retry_reuses_the_same_notification_and_exchange():
    # Arrange
    sent = json.loads(_invoke("agent:recipient", "retry rail", "--json").output)
    # Act -- a consumer may die after either poll; neither poll acknowledges.
    first = poll_inbox("recipient")
    second = poll_inbox("recipient")
    first_row = next(n for n in first if n.get("msg_id") == sent["message_id"])
    second_row = next(n for n in second if n.get("msg_id") == sent["message_id"])
    # Assert -- the retry cannot mint a second delivery identity.
    assert (
        first_row["id"],
        first_row["exchange_id"],
        second_row["exchange_id"],
    ) == (second_row["id"], sent["exchange_id"], sent["exchange_id"])


def test_timeout_after_commit_retry_returns_original_three_ids():
    # Arrange -- the first response is deliberately ignored, as after timeout.
    request_id = "req_timeout_after_commit"
    first = scitex_cards.dm_send(
        "agent:recipient",
        "commit then timeout",
        client_request_id=request_id,
    )
    # Act
    retried = scitex_cards.dm_send(
        "agent:recipient",
        "commit then timeout",
        client_request_id=request_id,
    )
    # Assert
    assert (
        retried["id"],
        retried["notification_id"],
        retried["exchange_id"],
    ) == (first["id"], first["notification_id"], first["exchange_id"])


def test_generated_cli_retry_key_is_surfaced_before_submission():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dm", "send", "agent:recipient", "generated"])
    # Assert
    assert result.stderr.startswith("client_request_id=req_")


def test_dry_run_writes_no_dm():
    # Arrange
    key = thread_key("test-suite", "recipient")
    # Act
    result = _invoke("agent:recipient", "preview", "--dry-run", "--json")
    stored = messages_in(key)
    # Assert
    assert result.exit_code == 0 and stored == []


def test_command_calls_only_the_public_dm_send_api_for_the_write():
    # Arrange
    source = inspect.getsource(subject.send_cmd.callback)
    # Act
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "scitex_cards"
    }
    # Assert
    assert names == {"scitex_cards.dm_send"}


# EOF
