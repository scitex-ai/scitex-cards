#!/usr/bin/env python3
"""Store failures that must say a lot to an operator and little to a stranger.

WHY THIS EXISTS. The canonical-store refusal carries a deliberately long message:
the absolute database path plus a paragraph explaining that a missing file is not
an empty store. That text is load-bearing - ``views.py``'s own comment records
that an earlier version let the refusal escape as an unparseable HTML error page,
so the board showed a bare "HTTP 500" with no cause, and putting the store's
sentence in the body is what "turns an outage into a diagnosis".

Then scitex-hub loaded https://scitex.ai/apps/cards/ in a browser as an
ANONYMOUS visitor and got that entire paragraph, including
``/app/.scitex/cards/cards.db``, rendered into the page. Correct as a log line;
wrong as a response to a stranger, who learns our filesystem layout and reads a
design rationale addressed to us.

So the message needs two audiences, and the failure needs a TYPE rather than a
prose blob that callers must string-match:

    str(exc)             the full diagnosis - logs, and the loopback board
    exc.public_summary   what a stranger may see

The choice between them is NOT made here. ``views.py`` makes it, keyed on
``settings.DEBUG`` - which is already correct on both sides without any new
configuration: the loopback board runs DEBUG=true and keeps its diagnosis, and
``SCITEX_CARDS_PUBLIC_HOST`` FORCES DEBUG=false (settings.py, not overridable),
so any publicly reachable deployment gets the summary. Re-using that switch means
there is no second flag to set wrongly.
"""

from __future__ import annotations

__all__ = ["StoreUnavailableError"]

#: Deliberately says nothing about paths, backends or reasons. A stranger learns
#: only that the server cannot serve them, which is all they need and all they
#: are entitled to.
_PUBLIC_SUMMARY = "The task store is not available on this server."


class StoreUnavailableError(RuntimeError):
    """The canonical store could not be read, and we refuse to invent one.

    A RuntimeError subclass on purpose: every existing ``except Exception``
    keeps catching it, so introducing the type breaks no caller. What it adds is
    the ability to catch THIS failure specifically, and a safe short form for
    audiences that are not us.
    """

    def __init__(self, detail: str, public_summary: str = _PUBLIC_SUMMARY) -> None:
        super().__init__(detail)
        #: Safe for an unauthenticated caller. Never interpolate a path here.
        self.public_summary = public_summary


# EOF
