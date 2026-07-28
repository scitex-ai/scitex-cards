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

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "scitex-todo-standalone-dev-key-not-for-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "0.0.0.0"]
# LAN / mobile access (opt-in). When the board is bound to 0.0.0.0 (via
# `scitex-cards board --host 0.0.0.0`) so a phone on the same network can reach
# it, Django still rejects the LAN Host header unless it is allowed here. Set
# SCITEX_CARDS_ALLOWED_HOSTS to a comma-separated list (e.g. "192.168.11.121",
# or "*" to allow any) to permit it. Default stays loopback-only. NOTE: the
# standalone board has no auth, so only open it on a trusted network; the hub
# (with auth) is the secure remote path.
_extra_hosts = os.environ.get("SCITEX_CARDS_ALLOWED_HOSTS", "").strip()
if _extra_hosts:
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

# EOF
