#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``tasks.status`` DDL default (pending abolition).

``SCHEMA_SQL`` still carried ``DEFAULT 'pending'`` after ``pending`` was
abolished 2026-07-10, while the Python write paths default to ``deferred``
(``add_task(status="deferred")`` in ``_store_add.py``, ``Task.status`` in
``_task.py``). A raw SQL INSERT omitting the column would therefore mint a
row in a status no live view serves and the validator only tolerates. The
DDL now defaults to ``deferred``; these pins keep the DDL, the Python
defaults, and the enum mirrors aligned.

These pins import modules and run ``sqlite3 :memory:`` only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import sqlite3

from scitex_cards._db_schema_sql import SCHEMA_SQL
from scitex_cards._store_add import add_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES, Task


def test_ddl_default_is_deferred_not_pending():
    assert re.search(
        r"status\s+TEXT\s+NOT NULL\s+DEFAULT\s+'deferred'", SCHEMA_SQL
    ), "tasks.status DDL default must be 'deferred'"
    assert "DEFAULT 'pending'" not in SCHEMA_SQL


def test_ddl_default_mirrors_python_write_defaults():
    add_default = inspect.signature(add_task).parameters["status"].default
    assert add_default == "deferred"
    (task_default,) = [
        f.default for f in dataclasses.fields(Task) if f.name == "status"
    ]
    assert task_default == "deferred"


def test_ddl_default_in_valid_outside_abolished():
    assert "deferred" in VALID_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "pending" in ABOLISHED_STATUSES


def test_raw_insert_omitting_status_lands_deferred():
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS tasks \(.*?\);", SCHEMA_SQL, re.DOTALL
    )
    assert match, "tasks CREATE TABLE statement must be extractable"
    con = sqlite3.connect(":memory:")
    con.execute(match.group(0))
    con.execute("INSERT INTO tasks(id, title) VALUES ('t1', 'T')")
    (status,) = con.execute(
        "SELECT status FROM tasks WHERE id = 't1'"
    ).fetchone()
    assert status == "deferred"
    con.close()
