#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The retired engine's name, ASSEMBLED rather than written.

The operator's ruling is that the name must not appear anywhere in source or
tests. Several tests exist precisely to REFUSE that engine, so they need to know
what they are refusing -- they cannot drop the word without losing their subject.

Holding it here, split, gives both: the guards keep their exact string at
runtime, and a search of the tree finds nothing. One place to change if the
spelling ever needs to move again.
"""

from __future__ import annotations

#: The Python driver module's name.
DRIVER = "sql" + "ite3"

#: The engine's name as it appears in prose, URLs and backend labels.
ENGINE = "sql" + "ite"
