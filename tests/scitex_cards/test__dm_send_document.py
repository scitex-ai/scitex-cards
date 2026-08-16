#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``dm_send_document`` — the entry point that did not exist.

An agent asked which API to use to send the operator a PDF. The honest answer
was "none": ``dm_send`` takes ``to`` and ``body``, and nothing else. The
receiving half already worked — operator-side uploads render in the chat pane
— so the gap was the SENDING half alone, and a real deliverable (three SOHO
application documents and a loan contract, on one day) reached the operator as
prose describing a document.

Shape mirrored from claude-code-telegrammer's ``send_document`` on purpose:
recipient, local file path, optional caption. An agent that knows how to hand
the operator a file over Telegram makes the same call here.

What these pin:

* the message that lands carries the stored url on ITS OWN LINE, which is how
  the chat pane recognises an attachment — the same convention operator-side
  uploads already produce, so one renderer serves both;
* the caption becomes the readable text, and without one the filename does,
  so the thread list never previews a bare url;
* refusals come back as an ``error`` the caller can act on, not an exception
  and not a silently-empty send.

Every test drives an EXPLICIT tmp store.
"""

from __future__ import annotations

import json

import anyio
import pytest

from scitex_cards import _threads
from scitex_cards._mcp_skills import dm_send_document


@pytest.fixture
def store(tmp_path):
    """An explicit tmp task-store path — never the resolved default."""
    return str(tmp_path / "cards.db")


@pytest.fixture
def agent_id(env):
    """A resolvable sender identity; the DM 'from' must name a real agent.

    Uses the `env` fixture (tests/scitex_cards/conftest.py) rather than
    `monkeypatch`: PA-306 forbids the fixture ecosystem-wide, and `env` is its
    sanctioned replacement — it saves each touched key, sets it for the test,
    and restores the prior value on teardown. Same guarantee, real os.environ.
    """
    env.set("SCITEX_CARDS_AGENT_ID", "scitex-cards")
    env.delete("SCITEX_CARDS_HUB_URL")
    return "scitex-cards"


@pytest.fixture
def source_pdf(tmp_path):
    path = tmp_path / "loan-contract.pdf"
    path.write_bytes(b"%PDF-1.4 bytes\n")
    return path


def _send(**kwargs):
    """Drive the async tool from a sync test."""
    return json.loads(anyio.run(lambda: dm_send_document(**kwargs)))


@pytest.fixture
def sent(agent_id, source_pdf, store):
    return _send(
        to="operator",
        file_path=str(source_pdf),
        caption="the loan contract you asked for",
        tasks_path=store,
    )


@pytest.fixture
def uncaptioned(agent_id, source_pdf, store):
    return _send(to="operator", file_path=str(source_pdf), tasks_path=store)


def test_the_document_reaches_the_operators_thread(sent, agent_id, store):
    # Arrange
    thread = _threads.get_thread(agent_id, "operator", store=store)
    # Act
    count = len(thread)
    # Assert
    assert count == 1


def test_the_message_is_authored_by_the_sending_agent(sent, agent_id):
    # Arrange — the record the tool returned.
    message = sent["message"]
    # Act
    author = message["from"]
    # Assert — never a blank or 'unknown' fallback.
    assert author == agent_id


def test_the_attachment_url_is_on_its_own_body_line(sent):
    """The chat pane splits attachments off by whole line; anything else hides."""
    # Arrange
    lines = sent["message"]["body"].split("\n")
    # Act
    url_line = lines[-1]
    # Assert
    assert url_line == sent["attachment"]["url"]


def test_the_body_line_carries_the_prefix_the_renderer_looks_for(sent):
    # Arrange
    lines = sent["message"]["body"].split("\n")
    # Act
    starts_with_prefix = lines[-1].startswith("attachments/")
    # Assert
    assert starts_with_prefix


def test_the_caption_becomes_the_message_text(sent):
    # Arrange
    lines = sent["message"]["body"].split("\n")
    # Act
    text = lines[0]
    # Assert
    assert text == "the loan contract you asked for"


def test_without_a_caption_the_filename_is_the_text(uncaptioned):
    """So the thread-list preview reads as a file, not as a raw url."""
    # Arrange
    lines = uncaptioned["message"]["body"].split("\n")
    # Act
    text = lines[0]
    # Assert
    assert text == "loan-contract.pdf"


def test_the_returned_attachment_reports_the_real_mime_type(sent):
    # Arrange — done by the `sent` fixture.
    # Act
    mime = sent["attachment"]["mime_type"]
    # Assert
    assert mime == "application/pdf"


def test_the_file_survives_the_source_being_deleted(sent, source_pdf, store):
    """Copied in, not referenced — the agent may clean up its scratch dir."""
    # Arrange
    source_pdf.unlink()
    from scitex_cards._attachments import resolve_stored

    subdir, token, name = sent["attachment"]["url"].split("/")[1:]
    # Act
    served = resolve_stored(subdir, token, name, store=store)
    # Assert
    assert served is not None


def test_a_missing_file_is_an_actionable_error(agent_id, store, tmp_path):
    """A refusal names what to fix; it does not raise through the MCP surface."""
    # Arrange
    absent = tmp_path / "nope.pdf"
    # Act
    result = _send(to="operator", file_path=str(absent), tasks_path=store)
    # Assert
    assert "error" in result


def test_a_missing_file_sends_no_message(agent_id, store, tmp_path):
    """A failed attach must not leave a message pointing at nothing."""
    # Arrange
    absent = tmp_path / "nope.pdf"
    _send(to="operator", file_path=str(absent), tasks_path=store)
    # Act
    thread = _threads.get_thread(agent_id, "operator", store=store)
    # Assert
    assert thread == []


def test_a_configured_hub_refuses_rather_than_storing_locally(
    agent_id, source_pdf, store, env
):
    """A local copy plus a remote message is a link to nothing — say so."""
    # Arrange
    env.set("SCITEX_CARDS_HUB_URL", "https://hub.example/api")
    # Act
    result = _send(to="operator", file_path=str(source_pdf), tasks_path=store)
    # Assert
    assert "error" in result


def test_the_tool_is_registered_on_the_mcp_surface():
    """It is useless to an agent unless the server actually exposes it."""
    # Arrange
    from scitex_cards._mcp_server import TOOL_NAMES

    # Act
    registered = "dm_send_document" in TOOL_NAMES
    # Assert
    assert registered


def test_the_path_taking_verb_is_not_reachable_over_http():
    """`_server.py` dispatches BACKEND_VERBS remotely; a path verb there would
    be an arbitrary-file read wearing a chat feature's clothes."""
    # Arrange
    from scitex_cards._backend import BACKEND_VERBS

    # Act
    exposed = "dm_send_document" in BACKEND_VERBS
    # Assert
    assert not exposed


# EOF
