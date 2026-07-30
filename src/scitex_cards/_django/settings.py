#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Django settings for the standalone scitex-todo board.

Used when running the board without a parent Django project (Route A in the
design doc — figrecipe parity, scitex-app optional). No database is configured
because the board is read-only over a YAML store; the task store on disk is the
only state.
"""

import importlib.util
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_DEV_SECRET_KEY = "scitex-todo-standalone-dev-key-not-for-production"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_SECRET_KEY)

# --------------------------------------------------------------------------
# Public exposure (Cloudflare Tunnel -> 127.0.0.1:8051)
#
# Setting SCITEX_CARDS_PUBLIC_HOST is the ONE switch that says "this board is
# reachable from the internet". Everything below keys off it, because the
# settings that make exposure safe are exactly the ones a person configuring
# a tunnel is not thinking about — they are thinking about the tunnel.
#
# The defaults here are correct for loopback and catastrophic in public:
# DEBUG=true serves tracebacks containing source, settings and environment to
# whoever asks, and the fallback SECRET_KEY is a literal in a public repo, so
# session and CSRF signatures are forgeable by anyone who reads it. Neither is
# a problem on 127.0.0.1 and both are a breach the moment a hostname resolves.
#
# So this block does not merely *permit* exposure, it makes the unsafe
# combination unreachable: DEBUG is forced off, and a missing DJANGO_SECRET_KEY
# raises at import rather than serving one request with a known key.
# --------------------------------------------------------------------------
PUBLIC_HOST = os.environ.get("SCITEX_CARDS_PUBLIC_HOST", "").strip()

# The board's own password (HTTP Basic), required before it may answer anything
# from outside this machine. See _board_auth.py for what Basic does and does not
# protect, and the SCITEX_CARDS_ALLOWED_HOSTS block below for the refusal that
# makes it mandatory rather than advisory.
BOARD_PASSWORD = os.environ.get("SCITEX_CARDS_PASSWORD", "").strip()

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "0.0.0.0"]

if PUBLIC_HOST:
    if SECRET_KEY == _DEV_SECRET_KEY:
        raise RuntimeError(
            "SCITEX_CARDS_PUBLIC_HOST is set (this board would be reachable "
            f"at {PUBLIC_HOST!r}) but DJANGO_SECRET_KEY is not — the board "
            "would sign sessions and CSRF tokens with a key published in the "
            "repository, so anyone could forge them. Generate one: "
            "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
        )

    ALLOWED_HOSTS = ALLOWED_HOSTS + [PUBLIC_HOST]

    # A POST must survive the proxy or the board is read-only in practice —
    # and "send a DM with an attachment from the phone" is the operator's
    # stated acceptance test, which is a POST.
    CSRF_TRUSTED_ORIGINS = [f"https://{PUBLIC_HOST}"]

    # cloudflared terminates TLS and forwards plain HTTP to the origin, so
    # request.is_secure() is False without this and Django will happily mark
    # session/CSRF cookies as non-secure over what the user sees as HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Not negotiable, and deliberately not an env override: there is no
    # legitimate reason to serve debug tracebacks on a public hostname.
    DEBUG = False
# LAN / mobile access (opt-in). When the board is bound to 0.0.0.0 (via
# `scitex-cards board --host 0.0.0.0`) so a phone on the same network can reach
# it, Django still rejects the LAN Host header unless it is allowed here. Set
# SCITEX_CARDS_ALLOWED_HOSTS to a comma-separated list (e.g. "192.168.11.121",
# or "*" to allow any) to permit it. Default stays loopback-only.
#
# THIS KNOB IS THE MOMENT THE OPERATING SYSTEM STOPS BEING THE ACCESS CONTROL.
# On 127.0.0.1 the board needs no auth because only local processes can reach
# it. Adding a LAN host removes that, and the board has no login of its own, so
# until this block existed the knob published every DM and every card -- readable
# AND writable, since the API is unauthenticated too -- to anyone on the network.
#
# So the knob now requires SCITEX_CARDS_PASSWORD and RAISES without one. The
# previous version of this comment said "the standalone board has no auth, so
# only open it on a trusted network", which is advice; advice is what you write
# when the code will still let you do the unsafe thing. The refusal is the fix
# and the comment is now just a description of it.
_extra_hosts = os.environ.get("SCITEX_CARDS_ALLOWED_HOSTS", "").strip()
if _extra_hosts:
    # The rule lives in _board_exposure so it can be tested as a function rather
    # than by reloading this module with a mutated environment. That module
    # imports nothing, which is a requirement and not a style choice: we are
    # inside Django's settings import here.
    from ._board_exposure import assert_exposure_is_authenticated

    assert_exposure_is_authenticated(_extra_hosts, BOARD_PASSWORD)
    ALLOWED_HOSTS += [h.strip() for h in _extra_hosts.split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "scitex_cards._django",
]

# Optional: scitex-ui shared shell components (static + templates served via
# AppDirectoriesFinder). Absent installs fall back to the bare React SPA.
try:
    import scitex_ui  # noqa: F401

    INSTALLED_APPS.append("scitex_ui")
except ImportError:
    pass

MIDDLEWARE = [
    # GZip FIRST so it wraps every response below it. /graph is ~5 MB of JSON
    # (measured 2026-07-10: 1180 cards; comments 1.9 MB = 38%, notes 0.84 MB
    # = 17%), refetched on every store change — and the store changes
    # constantly with a live fleet. Uncompressed that is the board's dominant
    # transfer cost and a large part of the operator's "遅すぎ". JSON of this
    # shape compresses roughly 10x. Semantics-free: no payload or handler
    # change, so it ships on its own. The structural fix (list payload
    # WITHOUT note/comments + a per-card detail fetch) is
    # todo-board-graph-payload-slim-20260710.
    "django.middleware.gzip.GZipMiddleware",
    # The password gate sits as high as it can while still letting GZip wrap the
    # response, so an unauthenticated request reaches no handler, touches no
    # store and does no work. It removes ITSELF (MiddlewareNotUsed) when no
    # password is configured, so the loopback default is unchanged and pays
    # nothing.
    "scitex_cards._django._board_auth.BoardPasswordMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "scitex_cards._django.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# Enable the scitex-ui Alt+I element inspector (DEBUG/staff-gated) on the
# board. The shell template already includes the partial; this context
# processor sets the gating flag it checks. Guard on the module actually
# existing (scitex-ui>=0.5.0) rather than just scitex-ui being installed,
# so an older scitex-ui degrades gracefully instead of raising on import.
if importlib.util.find_spec("scitex_ui.context_processors") is not None:
    TEMPLATES[0]["OPTIONS"]["context_processors"].append(
        "scitex_ui.context_processors.element_inspector"
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATICFILES_DIRS = [str(BASE_DIR / "static")]

# Stamp the release onto every static URL so an upgrade actually reaches the
# browser. Measured 2026-07-30: the operator's browser held a stale chat_menu.js
# and right-click stopped opening the context menu -- unreproducible anywhere
# else, fixed by a hard reload. Correctness must not depend on the user knowing
# to press Ctrl+Shift+R. Applied at the storage backend rather than at the 61
# `{% static %}` call sites, so it cannot be forgotten and new templates inherit
# it. STORAGES REPLACES its defaults wholesale on Django >= 4.2, so "default"
# must be restated here or file uploads lose their backend.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "scitex_cards._django.static_versioned.VersionedStaticFilesStorage",
    },
}

# EOF
