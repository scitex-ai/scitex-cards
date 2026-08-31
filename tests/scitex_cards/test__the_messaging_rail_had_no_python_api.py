#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Five operations existed only as MCP tool bodies — nothing could call them.

``dm_send``, ``dm_send_document``, ``dm_list``, ``poll_notifications`` and
``ack_notifications`` were async functions decorated with ``@mcp.tool()``, and
that was their ONLY form. The logic was welded to the transport: a cron job
could not send a DM, a script could not drain an inbox, and another package
could not confirm a notification, because reaching any of it meant speaking
MCP. Audit §6 names exactly this class.

What these pin:

* the five are importable and callable from the package root — the §6 contract
  is "a matching Python API", and an import that resolves is what that means;
* the Python API returns DICTS. A library that hands back JSON text charges
  every caller for the transport's encoding;
* the Python API RAISES where the tool returns ``{"error": ...}``. Silent
  error-dicts are how a caller sends nothing and hears nothing;
* AND THE MCP TOOLS STILL RETURN THEIR EXACT ERROR PAYLOADS. That is the
  control on the whole refactor: the extraction is only safe if the wire
  contract did not move, so the identity refusal is asserted through the tool,
  not just through the function;
* ``sender`` can be passed explicitly. PA-306 forbids `monkeypatch`, so a
  parameter — not an env poke — is how a caller drives a sender.

Every test drives an EXPLICIT tmp store; none touches the resolved default.
"""

from __future__ import annotations

import inspect
import json

import anyio
import pytest

import scitex_cards
from scitex_cards import _messaging

_FIVE = [
    "ack_notifications",
    "dm_list",
    "dm_send",
    "dm_send_document",
    "poll_notifications",
]


@pytest.fixture
def store(tmp_path, new_store):
    """An explicit tmp task-store path — never the resolved default."""
    return new_store()


@pytest.fixture
def agent_id(env):
    """A resolvable sender identity, via the sanctioned `env` fixture."""
    env.set("SCITEX_CARDS_AGENT_ID", "scitex-cards")
    env.delete("SCITEX_CARDS_HUB_URL")
    return "scitex-cards"


@pytest.fixture
def no_identity(env):
    """No resolvable identity — the DM 'from' field cannot be named."""
    env.delete("SCITEX_CARDS_AGENT_ID")
    env.delete("SCITEX_CARDS_HUB_URL")


@pytest.fixture
def sent(agent_id, store):
    """One DM sent through the PYTHON API (not the tool)."""
    return _messaging.dm_send("operator", "the suite is green", store=store)


@pytest.fixture
def two_sent(agent_id, store):
    """Two DMs, in order, so read-back ordering can be asserted."""
    _messaging.dm_send("operator", "first", store=store)
    _messaging.dm_send("operator", "second", store=store)
    return _messaging.dm_list(peer="operator", store=store)


def _run_tool(coro_fn, **kwargs):
    """Drive an async MCP tool from a sync test."""
    return json.loads(anyio.run(lambda: coro_fn(**kwargs)))


@pytest.fixture
def refusal_from_tool(no_identity, store):
    """What the MCP dm_send tool returns with no identity configured."""
    from scitex_cards._mcp_skills import dm_send as dm_send_tool

    return _run_tool(dm_send_tool, to="operator", body="x", tasks_path=store)


# --------------------------------------------------------------------------
# the §6 contract itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", _FIVE)
def test_each_tool_has_a_callable_python_api_at_the_package_root(name):
    # Arrange
    package = scitex_cards
    # Act
    fn = getattr(package, name, None)
    # Assert
    assert callable(fn), f"{name} has no callable Python API"


@pytest.mark.parametrize("name", _FIVE)
def test_each_one_is_advertised_in_dunder_all(name):
    # Arrange
    advertised = scitex_cards.__all__
    # Act
    present = name in advertised
    # Assert
    assert present


@pytest.mark.parametrize("name", _FIVE)
def test_the_python_api_is_not_the_coroutine_the_mcp_tool_wraps(name):
    """A caller must not have to run an event loop to send a DM.

    Guards the lazy-import table pointing at `_mcp_skills` instead of
    `_messaging` — that would satisfy "importable" while leaving every caller
    needing anyio.
    """
    # Arrange
    fn = getattr(scitex_cards, name)
    # Act
    is_async = inspect.iscoroutinefunction(fn)
    # Assert
    assert not is_async, f"{name} is still the async tool body"


# --------------------------------------------------------------------------
# dicts, not JSON text
# --------------------------------------------------------------------------


def test_dm_send_returns_a_dict_not_json_text(sent):
    # Arrange
    record = sent
    # Act
    kind = type(record)
    # Assert
    assert kind is dict


def test_dm_send_records_the_resolved_sender(sent, agent_id):
    # Arrange
    record = sent
    # Act
    who = record["from"]
    # Assert
    assert who == agent_id


def test_dm_send_records_the_body_verbatim(sent):
    # Arrange
    record = sent
    # Act
    body = record["body"]
    # Assert
    assert body == "the suite is green"


def test_dm_list_returns_the_messages_in_chronological_order(two_sent):
    # Arrange
    result = two_sent
    # Act
    bodies = [m["body"] for m in result["messages"]]
    # Assert
    assert bodies == ["first", "second"]


def test_poll_notifications_names_the_store_it_read(store):
    """The field that makes a poll/confirm split identifiable at all."""
    # Arrange
    agent = "scitex-cards"
    # Act
    result = _messaging.poll_notifications(agent, store=store)
    # Assert
    assert "store" in result


def test_poll_notifications_echoes_the_agent_it_was_asked_about(store):
    # Arrange
    agent = "scitex-cards"
    # Act
    result = _messaging.poll_notifications(agent, store=store)
    # Assert
    assert result["agent"] == agent


def test_ack_reports_an_id_the_inbox_never_held_as_unknown(store):
    """Idempotent, never an error — the payload distinguishes the case."""
    # Arrange
    stranger = "n_nosuchid"
    # Act
    result = _messaging.ack_notifications("scitex-cards", [stranger], store=store)
    # Assert
    assert result["unknown"] == [stranger]


def test_ack_confirms_nothing_for_an_id_the_inbox_never_held(store):
    # Arrange
    stranger = "n_nosuchid"
    # Act
    result = _messaging.ack_notifications("scitex-cards", [stranger], store=store)
    # Assert
    assert result["confirmed"] == []


# --------------------------------------------------------------------------
# raising, and the explicit sender that makes it testable
# --------------------------------------------------------------------------


def test_dm_send_raises_when_no_identity_is_configured(no_identity, store):
    # Arrange
    peer = "operator"
    # Act: the call itself is the act; the raise is the behaviour under test
    # Assert
    with pytest.raises(_messaging.AgentIdentityUnresolved):
        _messaging.dm_send(peer, "nobody sent this", store=store)


def test_the_identity_refusal_names_the_variable_to_set(no_identity, store):
    """A refusal that does not say what to do is half-written."""
    # Arrange
    captured = ""
    # Act
    try:
        _messaging.dm_send("operator", "x", store=store)
    except _messaging.AgentIdentityUnresolved as exc:
        captured = str(exc)
    # Assert
    assert "SCITEX_CARDS_AGENT_ID" in captured


def test_an_explicit_sender_overrides_the_environment_identity(agent_id, store):
    # Arrange
    other = "scitex-hub"
    # Act
    record = _messaging.dm_send("operator", "from someone else", store=store,
                                sender=other)
    # Assert
    assert record["from"] == other


def test_an_explicit_sender_works_with_no_environment_identity(no_identity, store):
    """The parameter is the monkeypatch-free way to drive a sender."""
    # Arrange
    other = "scitex-hub"
    # Act
    record = _messaging.dm_send("operator", "explicit", store=store, sender=other)
    # Assert
    assert record["from"] == other


# --------------------------------------------------------------------------
# THE CONTROL: the MCP tools' wire contract did not move
# --------------------------------------------------------------------------


def test_the_mcp_tool_still_returns_a_refusal_object_not_a_raise(refusal_from_tool):
    """The extraction is only safe if the tool's payload is unchanged."""
    # Arrange
    payload = refusal_from_tool
    # Act
    keys = set(payload)
    # Assert
    assert keys == {"error"}


def test_the_mcp_refusal_keeps_its_published_wording(refusal_from_tool):
    """Callers read this text; it names the env var AND the .mcp.json line."""
    # Arrange
    payload = refusal_from_tool
    # Act
    message = payload["error"]
    # Assert
    assert message.startswith("dm: no agent identity configured.")


def test_the_mcp_dm_list_tool_also_refuses_rather_than_raising(no_identity, store):
    # Arrange
    from scitex_cards._mcp_skills import dm_list as dm_list_tool

    # Act
    payload = _run_tool(dm_list_tool, peer="operator", tasks_path=store)
    # Assert
    assert set(payload) == {"error"}


def test_the_mcp_tool_still_delivers_a_normal_send(agent_id, store):
    """Over-reach control: refusals are not the only path that must survive."""
    # Arrange
    from scitex_cards._mcp_skills import dm_send as dm_send_tool

    # Act
    payload = _run_tool(dm_send_tool, to="operator", body="still works",
                        tasks_path=store)
    # Assert
    assert payload["body"] == "still works"


# EOF
