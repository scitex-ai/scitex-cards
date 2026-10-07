#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Database-migration family subpackage (first family move, Oct 2026 lease).

Owns the schema-rung helpers that used to sit flat at the package root
(``scitex_cards/_db_notification_exchange.py``,
``scitex_cards/_db_dm_idempotency.py``). The old modules remain as
compatibility shims re-exporting the same objects, so every existing
import path keeps working with identity and ``__module__`` attribution
preserved.

Deliberately inert: this package defines rung SQL and nothing else — no
ladder orchestration (that stays in ``_db_init_schema``), no trigger or
shape policy. Later families move in their own slices with their own
shims; nothing here reaches back out to the flat modules it replaces.
"""

# EOF
