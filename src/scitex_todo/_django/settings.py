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

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "scitex_todo._django",
]

# Optional: scitex-ui shared shell components (static + templates served via
# AppDirectoriesFinder). Absent installs fall back to the bare React SPA.
try:
    import scitex_ui  # noqa: F401

    INSTALLED_APPS.append("scitex_ui")
except ImportError:
    pass

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # HTTP Basic Auth gate driven by ``$SCITEX_TODO_BASIC_AUTH=user:password``.
    # When the env var is UNSET this middleware is a passthrough (localhost dev
    # unchanged); when SET it returns 401 for any request without valid
    # credentials. Required prerequisite when exposing the board off
    # ``127.0.0.1`` (e.g. via Cloudflare Tunnel) — the board's drag-reorder
    # and Resolve buttons write to the YAML store, so an unauthed reachable
    # board is read AND write open to anyone with the URL.
    # Sits BEFORE CommonMiddleware so the 401 short-circuits before any
    # URL-normalization redirects can leak the existence of protected paths.
    "scitex_todo._django._basic_auth.BasicAuthMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "scitex_todo._django.urls"

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
