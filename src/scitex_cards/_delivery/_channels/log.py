#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The slice-1 concrete channel: a stdlib-``logging`` delivery sink.

:class:`LogChannel` is the no-creds-needed channel that proves the delivery
loop end-to-end: it writes ONE human-readable line per notification via the
stdlib logging logger and reports ``sent``. It is registered under the
``scitex_cards.delivery_channels`` entry-point group (see pyproject.toml) so
the registry discovers it automatically, and it is the default address-less
channel every user can be given without any external configuration.
"""

from __future__ import annotations

import scitex_logging as slogging

from .._channel import DeliveryResult, Status

logger = slogging.getLogger(__name__)


class LogChannel:
    """Deliver a notification by emitting one ``logging`` record.

    Implements the :class:`~scitex_cards._delivery._channel.DeliveryChannel`
    Protocol. Stateless + creds-free — the canonical "always available"
    channel. ``address`` is optional (logging needs no destination).
    """

    #: Registry + ledger key for this channel.
    name = "log"

    def deliver(
        self,
        *,
        recipient: str,
        address: str,
        notification: dict,
    ) -> DeliveryResult:
        """Write a one-line record for ``notification`` and return ``sent``.

        The line carries the recipient, the notification id, its event type,
        the card it concerns, and the body — enough to audit "what went out"
        from a log scrape. Always returns ``sent`` (a logging emit that
        somehow raised would propagate and be caught by the loop as a
        retryable ``failed``).
        """
        note_id = notification.get("id", "?")
        event_type = notification.get("event_type", "?")
        card_id = notification.get("card_id", "?")
        body = notification.get("body", "")
        logger.info(
            "delivery[log] -> recipient=%s id=%s event=%s card=%s body=%s",
            recipient,
            note_id,
            event_type,
            card_id,
            body,
        )
        return DeliveryResult(
            status=Status.SENT,
            channel=self.name,
            detail=f"logged notification {note_id} for {recipient}",
        )


__all__ = ["LogChannel"]

# EOF
