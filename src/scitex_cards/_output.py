#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PS-220 content-rendering contract — verbatim product lines on stdout.

PS-220 (``source-uses-print-not-scitex-logging``) forbids a bare ``print`` in
shippable SciTeX source, with no configuration or comment bypass — including
for JSON and protocol payloads. The prescribed transport for output that IS
the product is ``scitex_logging.getPlainConsole(__name__).emit(payload)``,
which writes the message verbatim to stdout (plus newline) with no level
prefix, so byte contracts hold while sharing the logging discovery pattern.

This module is that transport, centralized: it exists for the card-id line
the git hooks read:
the hook does ``card_id=$(… card-id <branch> <msg>)`` and treats the bytes on
stdout as the card id, so routing that line through the LEVELLED logger
(``getLogger`` / ``getConsole``) would break it twice over — those write
console records to STDERR, and they prepend an ``INFO: ``/``WARN: `` level
prefix. The payload IS the product, so the transport must not decorate it.

It is deliberately NOT a general ``print`` hatch. Human-facing status and
diagnostics belong on ``scitex_logging.getLogger`` / ``getConsole``, which
carry the level, the aligned prefix and the searchable record the mandate
exists for. Use this only where the exact bytes on stdout are a published
contract with a consumer.
"""

from __future__ import annotations

import scitex_logging as slogging

__all__ = ["write_content"]


def write_content(content: str) -> None:
    """Write already-rendered content verbatim to stdout.

    Parameters
    ----------
    content : str
        The already-rendered product line, passed through unchanged. The
        enclosing API is an output operation and emits its caller's content
        as-is — no level prefix, no indentation, no trailing decoration.

    Returns
    -------
    None
    """
    slogging.getPlainConsole(__name__).emit(content)
