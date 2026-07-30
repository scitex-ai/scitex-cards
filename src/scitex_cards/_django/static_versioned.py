#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stamp the release version onto every static URL, so upgrades are visible.

WHY THIS EXISTS -- a measured user-visible failure, not a hygiene item
---------------------------------------------------------------------
2026-07-30: the operator reported that right-click stopped opening the context
menu on the DM page and selected text instead. It could not be reproduced --
against the live board in a fresh browser the menu opened correctly, nothing was
selected, and all 18 chat modules loaded. **A hard reload fixed it for them.**

That is the signature of a stale cached asset: their browser held an older
``chat_menu.js`` against a newer template, so a feature that works for everyone
else is broken for the one person actually using the board, and nobody can
reproduce it. Earlier the same day the same mechanism hid a whole release --
a 0.23.0 template referencing none of the 0.24.0 modules, so ``window.ChatAvatar``
was undefined, avatars silently fell back to "?" and the render-window fix never
ran, with zero console errors, for two days.

The remedy before this module was "the user knows to press Ctrl+Shift+R". That is
not a remedy; it makes correctness depend on what the user happens to know.

WHY THE STORAGE BACKEND AND NOT THE TEMPLATES
---------------------------------------------
There are 61 ``{% static %}`` references across chat.html (26), board_v3.html (33)
and standalone.html (2). Editing each one is 61 chances to miss one, and every
future template is a 62nd. Overriding ``url()`` puts the stamp at the single
choke point every reference already flows through, so it cannot be forgotten and
new templates get it for free. Same reasoning as fixing a reader rather than
asking every writer to behave.

WHY THE VERSION AND NOT A CONTENT HASH
--------------------------------------
A content hash (``ManifestStaticFilesStorage``) is stricter, but it requires a
``collectstatic`` step and a manifest this package does not have -- it serves
straight from its own ``static/`` directory. The board is deployed by installing
a release, and every release bumps ``__version__``, so the version changes
exactly when the files can change. It would have busted today's failure: the
operator went 0.23.0 -> 0.24.0 and still got the old JS.

The honest limit: editing a static file WITHOUT bumping the version does not
bust the cache. That is the development case, where a hard reload is a normal
tool, rather than the operator case, where it is not.
"""

from __future__ import annotations

from django.contrib.staticfiles.storage import StaticFilesStorage

__all__ = ["VersionedStaticFilesStorage", "append_version"]


def append_version(url: str, version: str | None) -> str:
    """Return ``url`` with ``?v=<version>`` appended.

    Pure, so the interesting behaviour is testable without Django.

    Returns ``url`` untouched when there is no usable version: a page that
    renders with un-stamped URLs is merely back to today's behaviour, whereas
    one that raises because a version string was missing is a worse outcome
    than the bug being fixed. Whitespace-only is treated as absent.

    An existing query string is preserved -- ``{% static %}`` does not normally
    produce one, but a storage subclass must not corrupt a URL it did not expect.
    """
    if not version or not version.strip():
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={version.strip()}"


def _release_version() -> str | None:
    """The installed release version, or None if it cannot be determined.

    Imported lazily and defensively: static URL construction runs on every
    template render, and it must never be the thing that breaks a page.
    """
    try:
        from scitex_cards import __version__

        return __version__
    except Exception:
        return None


class VersionedStaticFilesStorage(StaticFilesStorage):
    """``StaticFilesStorage`` that stamps the release onto every URL."""

    def url(self, name: str) -> str:
        return append_version(super().url(name), _release_version())


# EOF
