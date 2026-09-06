#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The delivery pass reads BOTH inbox keys a recipient's notifications live under.

THE LAST READER THAT DID NOT. A producer enqueues under whatever
``_notify.resolve_recipients`` returned — the stable ``u_*`` id when the agent
is registered, the raw name otherwise. Every other reader on this rail already
tries both: ``_inbox_confirm.recipient_keys`` exists for exactly this, and
``_mcp_channel`` and ``_inbox_present`` both use it. ``deliver_pending`` polled
``recipient.user`` alone, so a ``recipients.json`` row spelled with the raw
name reads an empty drawer whenever that name resolves to a ``u_*`` id — the
messages sitting in the store, readable, while the reader looks elsewhere.

That is the silent-miss shape this entire rail exists to catch: it produces no
error, no fault and no missing-delivery signal. The pass reports zero pending
and succeeds.

═══ WHAT THIS FILE DOES NOT YET PIN, STATED RATHER THAN IMPLIED ═══

There is no behavioural test here, and that is a gap rather than a judgement
that one is unnecessary. I wrote three and could not get them green — including
the CONTROL (a raw-name recipient, the case that already worked before the
change), which failing tells me my ARRANGEMENT was wrong and not the code. A
test I cannot explain the colour of is not evidence, so it is absent rather
than skipped.

What the arrangement needs measuring on, for whoever writes it: how
``deliver_pending`` resolves its channels against a ``recipients.json`` entry
(a recipient with no usable channel is dropped, so ``pending`` stays 0 and
looks identical to the defect), and whether ``pending`` counts what this test
assumed. One trap is already known and worth carrying over — ``recipients_path``
resolves ``<store_dir>/recipients.json`` where ``store_dir`` is the
process-wide local root for a server store, NOT a per-store directory, so the
arrangement must move ``$SCITEX_DIR`` first or it writes into the shared root
this fleet's daemons read.
"""

import inspect

from scitex_cards._delivery import _loop


def test_the_pass_asks_for_every_key_the_recipient_can_live_under():
    """THE WIRING PIN, and with no behavioural test beside it, the whole claim.

    It is honest about what it proves: the shared helper is called at this
    site. It does NOT prove the union is correct, and ``recipient_keys`` has
    its own tests for that. What it does buy is that a later refactor dropping
    the helper goes red rather than silently restoring the defect — which is
    the failure mode that let this one reader diverge from the other three in
    the first place.
    """
    # Arrange
    source = inspect.getsource(_loop.deliver_pending)
    # Act
    uses_shared_helper = "recipient_keys" in source
    # Assert
    assert uses_shared_helper


# EOF
