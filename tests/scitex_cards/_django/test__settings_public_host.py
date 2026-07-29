#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`SCITEX_CARDS_PUBLIC_HOST` makes the board safe to expose, or refuses.

The board's defaults are correct for 127.0.0.1 and catastrophic in public:
`DEBUG=true` serves tracebacks carrying source, settings and environment, and
the fallback `SECRET_KEY` is a literal in a public repository, so session and
CSRF signatures are forgeable by anyone who reads it.

The two load-bearing tests here are `test_debug_is_forced_off_...` and
`test_missing_secret_key_refuses_to_start`. They assert that the unsafe
combination is *unreachable* rather than merely discouraged — someone setting
up a tunnel is thinking about the tunnel, not about `DJANGO_DEBUG`.

Each case runs in a fresh interpreter because Django settings are read once at
import; reloading in-process would leak state between cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SETTINGS = "scitex_cards._django.settings"


def _probe(env_overrides: dict, expr: str):
    """Import settings in a fresh interpreter under `env_overrides`, eval `expr`.

    Returns ``{"ok": True, "value": ...}`` or ``{"ok": False, "error": str}``
    so a refusal is data to assert on rather than a crashed probe.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    for key in (
        "SCITEX_CARDS_PUBLIC_HOST",
        "DJANGO_SECRET_KEY",
        "DJANGO_DEBUG",
    ):
        env.pop(key, None)
    env.update({k: v for k, v in env_overrides.items() if v is not None})

    code = (
        "import json\n"
        "try:\n"
        f"    import {SETTINGS} as s\n"
        f"    print(json.dumps({{'ok': True, 'value': {expr}}}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'ok': False, 'error': str(exc)}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# Default posture — loopback, unchanged
# --------------------------------------------------------------------------


def test_without_public_host_allowed_hosts_stays_loopback():
    """No public host configured: behaviour must be exactly as before."""
    # Arrange
    env = {}

    # Act
    result = _probe(env, "s.ALLOWED_HOSTS")

    # Assert
    assert result["value"] == ["127.0.0.1", "localhost", "0.0.0.0"]


def test_without_public_host_debug_still_defaults_true():
    """Local development keeps its tracebacks."""
    # Arrange
    env = {}

    # Act
    result = _probe(env, "s.DEBUG")

    # Assert
    assert result["value"] is True


def test_without_public_host_dev_secret_key_is_tolerated():
    """The refusal must not fire on a loopback board with no key set."""
    # Arrange
    env = {}

    # Act
    result = _probe(env, "bool(s.SECRET_KEY)")

    # Assert
    assert result["ok"] is True


# --------------------------------------------------------------------------
# Exposed posture — the safety switches
# --------------------------------------------------------------------------


def test_public_host_is_added_to_allowed_hosts():
    """Without this the proxied hostname gets HTTP 400 DisallowedHost."""
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
    }

    # Act
    result = _probe(env, "s.ALLOWED_HOSTS")

    # Assert
    assert "cards.example.com" in result["value"]


def test_public_host_sets_csrf_trusted_origin_over_https():
    """A POST must survive the proxy: sending a DM is the acceptance test."""
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
    }

    # Act
    result = _probe(env, "s.CSRF_TRUSTED_ORIGINS")

    # Assert
    assert result["value"] == ["https://cards.example.com"]


def test_public_host_trusts_the_proxy_forwarded_proto():
    """cloudflared terminates TLS, so the origin sees plain HTTP."""
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
    }

    # Act
    result = _probe(env, "list(s.SECURE_PROXY_SSL_HEADER)")

    # Assert
    assert result["value"] == ["HTTP_X_FORWARDED_PROTO", "https"]


def test_public_host_marks_session_cookie_secure():
    """Otherwise the cookie rides plaintext on any downgrade."""
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
    }

    # Act
    result = _probe(env, "s.SESSION_COOKIE_SECURE")

    # Assert
    assert result["value"] is True


def test_public_host_marks_csrf_cookie_secure():
    """Same reasoning as the session cookie."""
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
    }

    # Act
    result = _probe(env, "s.CSRF_COOKIE_SECURE")

    # Assert
    assert result["value"] is True


# --------------------------------------------------------------------------
# The two that matter: unsafe combinations must be UNREACHABLE
# --------------------------------------------------------------------------


def test_debug_is_forced_off_even_when_explicitly_requested():
    """DJANGO_DEBUG=true must NOT win against a public hostname.

    This is the whole design: the person configuring a tunnel is thinking
    about the tunnel. A public board serving tracebacks leaks source,
    settings and environment to anyone who triggers a 500.
    """
    # Arrange
    env = {
        "SCITEX_CARDS_PUBLIC_HOST": "cards.example.com",
        "DJANGO_SECRET_KEY": "a-real-key",
        "DJANGO_DEBUG": "true",
    }

    # Act
    result = _probe(env, "s.DEBUG")

    # Assert
    assert result["value"] is False


def test_missing_secret_key_refuses_to_start():
    """Exposing the board with the repo's published key must not be possible.

    Refusing at import beats serving one request with a forgeable key.
    """
    # Arrange
    env = {"SCITEX_CARDS_PUBLIC_HOST": "cards.example.com"}

    # Act
    result = _probe(env, "s.SECRET_KEY")

    # Assert
    assert result["ok"] is False


def test_the_refusal_names_the_remedy():
    """An error that doesn't say how to fix it costs the reader a search."""
    # Arrange
    env = {"SCITEX_CARDS_PUBLIC_HOST": "cards.example.com"}

    # Act
    result = _probe(env, "s.SECRET_KEY")

    # Assert
    assert "token_urlsafe" in result["error"]


# EOF
