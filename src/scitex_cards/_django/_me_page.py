#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""My Cards" — the phone view of the viewer's OWN cards.

Card ``cards-gui-phone-view-own-cards-20260814``; the operator wants his cards
from his phone through scitex.ai (2026-08-14: 「scitex.ai がどこかでケータイから
カード使えるようになりますか？」→「それが1番綺麗」).

WHY THIS IS A SEPARATE PAGE FROM THE BOARD, since the card asked for that
choice to be justified rather than assumed. ``board_v3.html`` IS ALREADY
RESPONSIVE -- ``board_v3/05-responsive.css`` is a full ``max-width: 768px``
pass with a single-column shell override, scroll-snap columns, a drawer and
safe-area insets, and the shell supplies the viewport meta that activates it.
So the missing half was never layout. It was IDENTITY plus a focused query.
Answering "what is on my plate?" through a 3600-line desktop kanban means a
phone downloading the whole fleet, its graph and its timeline in order to show
eight cards. ``chat.html`` reached the same conclusion for the same reason, and
this page reuses its proven shape: a small template, a pure node-tested
decision module, and a DOM module that fetches.

WHY ITS OWN MODULE rather than a fourth page view in ``views.py``: that file is
481 lines against a 512-line cap, and this view plus the docstring explaining
it would push it over. A feature that arrives with its own file does not spend
somebody else's remaining budget.

THIS MODULE DECIDES NOTHING ABOUT ACCESS. Whatever gate stands in front of the
board (``BoardPasswordMiddleware`` standalone, hub's login when mounted there)
stands in front of this page too. WHICH CARDS it shows is
``handlers.mine``'s decision, and that handler refuses rather than guessing
when it cannot tell who is asking.
"""

from __future__ import annotations

from django.http import HttpResponse

from .views import _include_root

__all__ = ["ME_ALIASES", "me_page"]

#: Every route that serves this page. Its JSON lives one segment DEEPER, at
#: ``me/cards`` -- the same page/data shape ``dm`` and ``dm/threads`` already
#: use. A sibling ``/mine`` was the first spelling and was rejected before it
#: shipped: ``/me`` and ``/mine`` differ by one letter while being a page and
#: an API, which is a footgun in every log, bug report and address bar that
#: ever quotes one of them.
ME_ALIASES: tuple[str, ...] = ("me",)


def me_page(request):
    """Render the "My Cards" page."""
    from django.template.loader import render_to_string

    try:
        from scitex_cards import __version__ as _version
    except Exception:  # noqa: BLE001
        _version = "?"

    # Mount-aware API base -- the same contract the board and DM pages carry.
    # The page is served at "<include-root>me" and its JSON at
    # "<include-root>me/cards", so stripping this page's own trailing segment
    # recovers the include root every fetch and in-app link must be prefixed
    # with ("/apps/cards/" on the hub, "/" standalone). Hardcoding a
    # root-absolute path here is the bug the board has already shipped twice
    # (#556, #557); me.js reads this off the body and refuses to run without
    # it rather than silently guessing "/".
    api_base = _include_root(request.path, ME_ALIASES)

    html = render_to_string(
        "scitex_cards/me.html",
        {"scitex_cards_version": _version, "api_base": api_base},
        request=request,
    )
    return HttpResponse(html)


# EOF
