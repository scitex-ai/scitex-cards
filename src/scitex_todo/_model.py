#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator/writer for scitex-todo.

The task store is a YAML document with a top-level ``tasks:`` list. Each
task is a mapping with ``id`` + ``title`` + ``status`` (required) and
optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority`` /
``parent`` fields. ``priority`` is an explicit integer rank (lower = higher
priority); when absent, document order is the implicit ordering. ``parent``
is an optional task-id string that nests this task under another node — a
task's children are tasks whose ``parent`` equals this task's ``id`` (the
board's drill-down view follows this relation).

This module is the single validation gate: ``load_tasks`` raises
``TaskValidationError`` on a malformed store (missing id/title, duplicate
id, invalid status, non-integer priority, non-string parent) so downstream
adapters can assume well-formed input. ``save_tasks`` re-runs the same gate
before writing back and preserves the hand-written YAML comments +
structure via ruamel.yaml.

THIS FILE IS A THIN ORCHESTRATOR. The actual implementation was split out
(512-line-cap refactor) into:

  - ``_model_enums.py``    — VALID_STATUSES / VALID_KINDS / VALID_BLOCKERS /
    ``_BLOCKER_ALIASES``.
  - ``_model_task.py``     — the ``Task`` dataclass (``from_dict`` /
    ``to_dict``).
  - ``_model_deadline.py`` — ``Repeater`` + deadline/repeater parsing +
    ``next_deadline_for_task`` / ``is_overdue``.
  - ``_model_validate.py`` — ``TaskValidationError`` + ``_validate_tasks``.
  - ``_model_io.py``       — ``load_tasks`` / ``load_doc`` / ``save_tasks`` /
    the crash-safe writer + the store lock.

Every name that used to live directly in this module is re-exported below
so existing ``from ._model import X`` / ``from scitex_todo._model import X``
/ ``_model.X`` call sites keep resolving unchanged.
"""

from __future__ import annotations

from ._model_deadline import (  # noqa: F401  hook-bypass: line-limit
    Repeater,
    _add_period,
    _get_repeater_rx,
    _last_day_of_month,
    _parse_deadline_or_raise,
    _parse_iso_date_or_raise,
    _pick_next_dt,
    is_overdue,
    next_deadline_for_task,
)
from ._model_enums import (  # noqa: F401  hook-bypass: line-limit
    VALID_BLOCKERS,
    VALID_KINDS,
    VALID_STATUSES,
    _BLOCKER_ALIASES,
)
from ._model_io import (  # noqa: F401  hook-bypass: line-limit
    _git_autocommit_store,
    _save_doc_unlocked,
    _save_tasks_unlocked,
    _store_lock,
    load_doc,
    load_tasks,
    save_tasks,
)
from ._model_task import Task  # noqa: F401  hook-bypass: line-limit
from ._model_validate import (  # noqa: F401  hook-bypass: line-limit
    TaskValidationError,
    _validate_tasks,
)

# EOF
