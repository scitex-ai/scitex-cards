#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``{{ api_base|stx_path:"/projects" }}`` — a mount-relative URL that cannot
turn into a protocol-relative one.

WHY THIS EXISTS. The board builds its links and form actions from ``api_base``,
the include root the hub mounts the app under (``""`` at the root mount,
``"/apps/cards"`` behind the hub). The obvious spelling,
``{{ api_base }}/projects``, is CORRECT at a sub-path mount and WRONG at the root
mount: it renders ``//projects``, and a browser reads a leading ``//`` as
``https://projects`` — a host name. Measured 2026-09-17 on the card page link:
clicking a card left the browser on ``chrome-error://chromewebdata/`` with no
request ever made to this app, because the link never pointed at this app.

That is a defect no server-side test sees on its own: the page returns 200, the
``data-stx-*`` hook is present, and the text is right. It took a browser click to
find, and it is now pinned by tests that assert the rendered href and action are
ROOT-ABSOLUTE at both mounts.
"""

from __future__ import annotations

from django import template
from django.utils.html import conditional_escape

register = template.Library()


@register.filter
def stx_path(base, path: str = "") -> str:
    """Join a mount root and a path into one root-absolute URL.

    ``("" , "/projects") -> "/projects"`` and
    ``("/apps/cards", "/projects") -> "/apps/cards/projects"`` — the case the
    naive concatenation gets wrong is the first one, and it gets it wrong by
    producing a URL that silently points at another host.
    """
    root = str(base or "").rstrip("/")
    leaf = str(path or "")
    if leaf and not leaf.startswith("/"):
        leaf = "/" + leaf
    return conditional_escape(f"{root}{leaf}")
