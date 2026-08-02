#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the slice-3 Telegram delivery channel.

NO mocks (STX-NM / PA-306): the wire is exercised through a REAL fake
``http_post`` recorder/responder injected via the ``http_post=`` seam. Covers
the spec's required cases:

* (a) 200 → ``sent``, and the POST got the right URL (token in path) +
      ``chat_id`` + ``text``.
* (b) 429 with ``Retry-After`` → ``failed`` with ``retry_after`` set.
* (c) 403 → ``failed`` with ``retry_after`` None.
* (d) a urllib raise inside ``deliver`` → ``failed`` (caught here).
* (e) missing token → ``failed`` with the clear, fail-loud detail.

Plus: 5xx is retryable, and a 429 with the hint only in the JSON body still
parses ``retry_after`` from ``parameters.retry_after``.

One assertion per test (STX-TQ007): ``_deliver`` re-runs the same round trip
for each property under test.
"""

from __future__ import annotations

import json
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from scitex_cards._delivery._channel import Status
from scitex_cards._delivery._channels.telegram import TOKEN_ENV, TelegramChannel


class FakeHttp:
    """A REAL recorder/responder for the ``http_post`` seam (not a mock).

    Records every ``(url, data)`` call and returns a pre-programmed
    ``(status_code, headers, body)`` triple — or RAISES a pre-programmed
    exception to exercise the network-error path.
    """

    def __init__(self, *, status=200, headers=None, body="", raises=None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, url: str, data: bytes):
        self.calls.append({"url": url, "data": data})
        if self.raises is not None:
            raise self.raises
        return self.status, self.headers, self.body


def _note(body="Card c1 reassigned to you", **over):
    n = {
        "id": "n_1",
        "event_type": "reassigned",
        "card_id": "c1",
        "body": body,
    }
    n.update(over)
    return n


def _deliver(*, token="TKN", notification=None, **http_kw):
    """One real deliver() round trip; returns ``(result, http_recorder)``."""
    http = FakeHttp(**http_kw)
    chan = TelegramChannel(token=token, http_post=http)
    result = chan.deliver(
        recipient="u",
        address="9",
        notification=_note() if notification is None else notification,
    )
    return result, http


def _form_of(http) -> dict:
    """The parsed form body of the first recorded POST."""
    return parse_qs(http.calls[0]["data"].decode("utf-8"))


@pytest.fixture
def no_telegram_token(env):
    """No override token AND no env var — the fail-loud path."""
    env.delete(TOKEN_ENV)


@pytest.fixture
def env_telegram_token(env):
    """A bot token supplied only through the environment."""
    env.set(TOKEN_ENV, "ENVTKN")
    return "ENVTKN"


# --------------------------------------------------------------------------- #
# (a) 200 → sent, with the right URL + chat_id + text                         #
# --------------------------------------------------------------------------- #
def test_success_200_reports_status_sent():
    # Arrange
    # Act
    result, _http = _deliver(status=200, body='{"ok": true}')
    # Assert
    assert result.status is Status.SENT


def test_success_200_names_the_telegram_channel():
    # Arrange
    # Act
    result, _http = _deliver(status=200, body='{"ok": true}')
    # Assert
    assert result.channel == "telegram"


def test_success_200_carries_no_retry_after():
    # Arrange
    # Act
    result, _http = _deliver(status=200, body='{"ok": true}')
    # Assert
    assert result.retry_after is None


def test_success_200_posts_exactly_once():
    # Arrange
    # Act
    _result, http = _deliver(status=200, body='{"ok": true}')
    # Assert
    assert len(http.calls) == 1


def test_telegram_post_uses_the_https_scheme():
    # Arrange
    _result, http = _deliver(status=200, body='{"ok": true}')
    # Act
    parsed = urlparse(http.calls[0]["url"])
    # Assert
    assert parsed.scheme == "https"


def test_telegram_post_targets_the_telegram_api_host():
    # Arrange
    _result, http = _deliver(status=200, body='{"ok": true}')
    # Act
    parsed = urlparse(http.calls[0]["url"])
    # Assert
    assert parsed.netloc == "api.telegram.org"


def test_telegram_post_carries_the_token_in_the_url_path():
    # Arrange
    _result, http = _deliver(token="TKN123", status=200, body='{"ok": true}')
    # Act
    parsed = urlparse(http.calls[0]["url"])
    # Assert
    assert parsed.path == "/botTKN123/sendMessage"


def test_telegram_form_body_carries_the_chat_id():
    # Arrange
    _result, http = _deliver(status=200, body='{"ok": true}')
    # Act
    form = _form_of(http)
    # Assert
    assert form["chat_id"] == ["9"]


def test_telegram_form_body_carries_the_notification_text():
    # Arrange
    _result, http = _deliver(status=200, body='{"ok": true}')
    # Act
    form = _form_of(http)
    # Assert
    assert form["text"] == ["Card c1 reassigned to you"]


def test_text_falls_back_to_event_and_card_when_body_empty():
    # Arrange
    _result, http = _deliver(
        status=200, body='{"ok": true}', notification=_note(body="")
    )
    # Act
    form = _form_of(http)
    # Assert
    assert form["text"] == ["[reassigned] card c1"]


# --------------------------------------------------------------------------- #
# (b) 429 with Retry-After header → failed + retry_after set                  #
# --------------------------------------------------------------------------- #
def test_429_with_retry_after_header_is_failed():
    # Arrange
    # Act
    result, _http = _deliver(
        status=429,
        headers={"Retry-After": "37"},
        body='{"ok": false, "error_code": 429}',
    )
    # Assert
    assert result.status is Status.FAILED


def test_429_retry_after_header_is_parsed_into_the_result():
    # Arrange
    # Act
    result, _http = _deliver(
        status=429,
        headers={"Retry-After": "37"},
        body='{"ok": false, "error_code": 429}',
    )
    # Assert
    assert result.retry_after == 37.0


def test_429_detail_names_the_status_code():
    # Arrange
    # Act
    result, _http = _deliver(
        status=429,
        headers={"Retry-After": "37"},
        body='{"ok": false, "error_code": 429}',
    )
    # Assert
    assert "429" in (result.detail or "")


def test_429_with_json_parameters_retry_after_is_failed():
    # Arrange
    body = json.dumps(
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 60}}
    )
    # Act
    result, _http = _deliver(status=429, headers={}, body=body)
    # Assert
    assert result.status is Status.FAILED


def test_429_json_parameters_retry_after_is_parsed():
    # Arrange
    body = json.dumps(
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 60}}
    )
    # Act
    result, _http = _deliver(status=429, headers={}, body=body)
    # Assert
    # the hint lives only in the JSON body here.
    assert result.retry_after == 60.0


# --------------------------------------------------------------------------- #
# (c) 403 → failed, retry_after None                                          #
# --------------------------------------------------------------------------- #
def test_403_reports_status_failed():
    # Arrange
    # Act
    result, _http = _deliver(
        status=403,
        body='{"ok": false, "error_code": 403, "description": "bot blocked"}',
    )
    # Assert
    assert result.status is Status.FAILED


def test_403_carries_no_retry_after():
    # Arrange
    # Act
    result, _http = _deliver(
        status=403,
        body='{"ok": false, "error_code": 403, "description": "bot blocked"}',
    )
    # Assert
    assert result.retry_after is None


def test_403_detail_names_the_status_code():
    # Arrange
    # Act
    result, _http = _deliver(
        status=403,
        body='{"ok": false, "error_code": 403, "description": "bot blocked"}',
    )
    # Assert
    assert "403" in (result.detail or "")


def test_other_4xx_reports_status_failed():
    # Arrange
    # Act
    result, _http = _deliver(status=400, body='{"ok": false, "error_code": 400}')
    # Assert
    assert result.status is Status.FAILED


def test_other_4xx_carries_no_retry_after():
    # Arrange
    # Act
    result, _http = _deliver(status=400, body='{"ok": false, "error_code": 400}')
    # Assert
    assert result.retry_after is None


def test_other_4xx_detail_names_the_status_code():
    # Arrange
    # Act
    result, _http = _deliver(status=400, body='{"ok": false, "error_code": 400}')
    # Assert
    assert "400" in (result.detail or "")


# --------------------------------------------------------------------------- #
# 5xx → failed (retryable), no retry_after                                    #
# --------------------------------------------------------------------------- #
def test_5xx_reports_status_failed():
    # Arrange
    # Act
    result, _http = _deliver(status=502, body="bad gateway")
    # Assert
    assert result.status is Status.FAILED


def test_5xx_carries_no_retry_after_and_stays_retryable():
    # Arrange
    # Act
    result, _http = _deliver(status=502, body="bad gateway")
    # Assert
    assert result.retry_after is None


def test_5xx_detail_names_the_status_code():
    # Arrange
    # Act
    result, _http = _deliver(status=502, body="bad gateway")
    # Assert
    assert "502" in (result.detail or "")


# --------------------------------------------------------------------------- #
# (d) network/urllib raise inside deliver → caught as failed                  #
# --------------------------------------------------------------------------- #
def test_network_error_reports_status_failed():
    # Arrange
    # Act
    result, _http = _deliver(raises=urllib.error.URLError("connection reset"))
    # Assert
    assert result.status is Status.FAILED


def test_network_error_carries_no_retry_after():
    # Arrange
    # Act
    result, _http = _deliver(raises=urllib.error.URLError("connection reset"))
    # Assert
    assert result.retry_after is None


def test_network_error_detail_names_a_transport_error():
    # Arrange
    # Act
    result, _http = _deliver(raises=urllib.error.URLError("connection reset"))
    # Assert
    assert "transport error" in (result.detail or "")


# --------------------------------------------------------------------------- #
# (e) missing token → failed with the clear, fail-loud detail                 #
# --------------------------------------------------------------------------- #
def _deliver_without_token():
    """Deliver with NO override token; returns ``(result, http_recorder)``."""
    http = FakeHttp(status=200)
    chan = TelegramChannel(http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())
    return result, http


def test_missing_token_reports_status_failed(no_telegram_token):
    # Arrange
    # Act
    result, _http = _deliver_without_token()
    # Assert
    assert result.status is Status.FAILED


def test_missing_token_carries_no_retry_after(no_telegram_token):
    # Arrange
    # Act
    result, _http = _deliver_without_token()
    # Assert
    assert result.retry_after is None


def test_missing_token_detail_names_the_env_var(no_telegram_token):
    # Arrange
    # Act
    result, _http = _deliver_without_token()
    # Assert
    assert TOKEN_ENV in (result.detail or "")


def test_missing_token_detail_says_there_is_no_token(no_telegram_token):
    # Arrange
    # Act
    result, _http = _deliver_without_token()
    # Assert
    assert "no telegram bot token" in (result.detail or "")


def test_missing_token_never_touches_the_wire(no_telegram_token):
    # Arrange
    # Act
    _result, http = _deliver_without_token()
    # Assert
    # no token → no POST.
    assert http.calls == []


def test_env_token_delivery_reports_status_sent(env_telegram_token):
    # Arrange
    http = FakeHttp(status=200, body='{"ok": true}')
    chan = TelegramChannel(http_post=http)  # no override
    # Act
    result = chan.deliver(recipient="u", address="9", notification=_note())
    # Assert
    assert result.status is Status.SENT


def test_env_token_is_resolved_lazily_into_the_url(env_telegram_token):
    # Arrange
    http = FakeHttp(status=200, body='{"ok": true}')
    chan = TelegramChannel(http_post=http)  # no override
    chan.deliver(recipient="u", address="9", notification=_note())
    # Act
    parsed = urlparse(http.calls[0]["url"])
    # Assert
    assert parsed.path == f"/bot{env_telegram_token}/sendMessage"


# EOF
