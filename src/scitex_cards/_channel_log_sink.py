#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit, opt-in log sink for the MCP channel server.

WHY THIS MODULE EXISTS — an instrument you cannot read is not an instrument.

``_mcp_channel`` calls ``logging.getLogger(__name__)`` and nothing ever attaches
a handler or sets a level. Under stdio-MCP the server is a CHILD of the Claude
session, so its stderr is not somewhere an operator (or an agent debugging
itself) can conveniently read either. The practical consequence, measured
2026-08-02: the per-tick timing instrument shipped in 0.31.5
(:mod:`._channel_tick_timing`) emits at DEBUG, which the root logger discards
outright — so the numbers that would settle the DM-latency question were being
computed on every tick and then thrown away.

That is the same defect class as the fail-soft ``except`` that hid the
``IS NOT DISTINCT FROM`` breakage for 36 hours: work is performed, evidence is
produced, and nothing can observe it.

FAIL LOUD, DELIBERATELY. If ``$SCITEX_CARDS_CHANNEL_LOG`` is set the caller has
asked for a log at a specific path; if that path cannot be written we RAISE
rather than degrade to no logging. A silently-absent log sink is precisely the
failure this module exists to remove, so it must not be this module's own
failure mode. An UNSET variable is not an error — no sink is the default.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

__all__ = [
    "ENV_CHANNEL_LOG",
    "install_channel_log_sink",
    "sink_path_from_env",
]

#: Env var naming a file to write channel logs to. Unset => no sink (default).
ENV_CHANNEL_LOG = "SCITEX_CARDS_CHANNEL_LOG"

#: Logger whose records the sink captures — the package root, so sibling
#: modules (``_channel_tick_timing`` consumers, ``_channel_drain_state``) are
#: covered without each needing to know a sink exists.
_PACKAGE_LOGGER = "scitex_cards"

#: Marker attribute stamped on handlers we install, so a second call is a
#: no-op instead of duplicating every line.
_MARKER = "_scitex_cards_channel_sink"

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def sink_path_from_env(env: dict[str, str] | None = None) -> Path | None:
    """Resolve the configured sink path, or ``None`` when unconfigured.

    A whitespace-only value counts as unset: it is far likelier to be an empty
    shell expansion than a deliberate request to log to a file named " ".
    """
    source = os.environ if env is None else env
    raw = (source.get(ENV_CHANNEL_LOG) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def install_channel_log_sink(
    env: dict[str, str] | None = None,
    *,
    level: int = logging.DEBUG,
) -> Path | None:
    """Attach a file handler for channel logs when one is configured.

    Returns the sink path, or ``None`` when ``$SCITEX_CARDS_CHANNEL_LOG`` is
    unset. Idempotent — calling twice does not double every record.

    Raises ``OSError`` when a CONFIGURED path cannot be created or opened. That
    is deliberate: see the module docstring. The caller asked for a log; giving
    them silence instead would reproduce the bug this exists to expose.
    """
    path = sink_path_from_env(env)
    if path is None:
        return None

    logger = logging.getLogger(_PACKAGE_LOGGER)

    for existing in logger.handlers:
        if getattr(existing, _MARKER, None) == str(path):
            # Already installed for this exact path — nothing to do.
            return path

    # Create the parent eagerly so a missing directory fails HERE, with the
    # path in the traceback, rather than on the first record written.
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(level)
    setattr(handler, _MARKER, str(path))

    logger.addHandler(handler)
    # The logger's own level gates records BEFORE handlers see them, so a
    # handler at DEBUG under a WARNING logger still yields nothing. This is the
    # exact trap that made the tick instrument unreadable.
    if logger.level == logging.NOTSET or logger.level > level:
        logger.setLevel(level)

    return path


# EOF
