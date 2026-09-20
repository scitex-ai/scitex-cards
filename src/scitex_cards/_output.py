#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PS-220 content-rendering contract — verbatim product lines on stdout.

PS-220 (``source-uses-print-not-scitex-logging``) forbids a bare ``print`` in
shippable SciTeX source, because library code that writes unconditionally to
stdout cannot be silenced, redirected or captured by a caller. It spares
exactly three *mechanically provable* transports:

* a recognized serializer call (``json.dumps(...)``, ``.to_json()``,
  ``.model_dump_json()``) — machine-readable data transport;
* a caller-owned REQUIRED stream (``print(text, file=<required param>)``);
* an explicit content-rendering API: a function whose whole job is to emit
  its caller-supplied content verbatim.

This module is the third. It exists for the card-id line the git hooks read:
the hook does ``card_id=$(… card-id <branch> <msg>)`` and treats the bytes on
stdout as the card id, so routing that line through the logger would break it
twice over — scitex-logging writes console records to STDERR, and it prepends
an ``INFO: ``/``WARN: `` level prefix. The payload IS the product, so the
transport must not decorate it.

It is deliberately NOT a general ``print`` hatch. Human-facing status and
diagnostics belong on ``scitex_logging.getLogger`` / ``getConsole``, which
carry the level, the aligned prefix and the searchable record the mandate
exists for. Use this only where the exact bytes on stdout are a published
contract with a consumer.
"""

from __future__ import annotations

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
    print(content)
