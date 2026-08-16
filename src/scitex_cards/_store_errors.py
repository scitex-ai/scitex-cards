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

__all__ = ["StoreNotProvisionedError", "StoreUnavailableError"]

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


#: What a stranger may learn when there is simply no store here yet. Says
#: "not set up", never "broken", because those are different answers and this
#: type exists precisely to stop them sharing one.
_NOT_PROVISIONED_SUMMARY = "No task store has been set up for this workspace yet."


class StoreNotProvisionedError(StoreUnavailableError):
    """No store EXISTS for this target yet — a normal per-tenant state.

    A SUBCLASS BECAUSE THE PARENT IS TOO COARSE TO BE A DISCRIMINATOR, and the
    coarseness was invisible until someone asked what the parent actually
    covers. ``StoreUnavailableError`` is raised for BOTH of these:

        the store does not exist yet          a tenant who has never had one
        connect() to PostgreSQL FAILED        the database server is DOWN

    Those are opposite answers. The first is a configuration state that should
    render onboarding and must not be retried; the second is an outage that
    must stay in 5xx monitoring and must be retried. A view keyed on the parent
    type cannot tell them apart, so classifying absence as 4xx would silently
    reclassify a REAL OUTAGE as "there is simply nothing here" — dropping it
    out of alerting and rendering a setup page over a dead database.

    THAT INVERSION IS WORSE THAN THE BUG IT WOULD FIX. Today a non-outage looks
    like an outage: noisy, and everyone sees it. The inverted form is silent,
    and silence is indistinguishable from health.

    Caught by scitex-hub while preparing the status-code change, against a
    positive control that could not catch it: the ``corrupt_store`` fixture
    raises ``DatabaseError`` — a DIFFERENT type — so it exercised a path that
    was never in doubt and went green while the path that SHARES the type went
    untested. The lesson is now a rule in this package: a control must fail by
    the SAME MECHANISM as the hazard, not merely in the same neighbourhood.
    ``test_an_unreachable_postgres_is_still_a_server_fault`` is that control —
    it points at a CLOSED PORT, so it raises through the identical call.

    THE ARGUMENT IS CORRECTNESS, NOT FREQUENCY, and the distinction is worth
    stating because the frequency evidence was retracted while this was being
    written. scitex-db measured three cold connects at 12-22s on 2026-08-05 and
    0.13s on 2026-08-09, same DSN and server; they withdrew the slow figure as
    a settled fact and restated it as intermittent with an unknown trigger.
    That is the measurement. The inference that it therefore fails often was
    not theirs and is not supported.

    None of which changes this type's justification. Reporting "the database is
    unreachable" as "no store here" is a wrong answer whether it fires hourly
    or once a year — and a RARE silent inversion is arguably the worse one,
    because nobody has seen it before, nobody recognises it when it fires, and
    the monitoring that would have caught it was switched off by the same
    change that caused it. Rarity is also why the closed-port test matters more
    rather than less: an intermittent fault with an unknown trigger is exactly
    what you cannot rely on catching in the wild.

    Safe to introduce for the same reason the parent was: every existing
    ``except StoreUnavailableError`` keeps catching it, so no caller breaks.
    Callers that want the distinction opt IN by naming this type.
    """

    def __init__(
        self, detail: str, public_summary: str = _NOT_PROVISIONED_SUMMARY
    ) -> None:
        super().__init__(detail, public_summary)


# EOF
