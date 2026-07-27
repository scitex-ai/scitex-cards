#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + loader/validator/writer for scitex-todo.

The task store is the SQLite database; this module models it as a
top-level ``tasks:`` list for the validation + adapter layers built on
top. Each task is a mapping with ``id`` + ``title`` + ``status`` (required) and
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
before writing back.
"""

from __future__ import annotations

from pathlib import Path

from ._task import VALID_STATUSES, TaskValidationError  # noqa: F401
from ._validate import _validate_tasks  # noqa: F401


def load_tasks(path: str | Path) -> list[dict]:
    """Load and validate the task list from the store.

    Parameters
    ----------
    path : str or pathlib.Path
        The logical store identity. ACCEPTED AND IGNORED: the database is
        resolved from the environment, and since the validation label started
        naming the database actually read, this value reaches no error text
        either. It survives as the signature every CRUD verb passes through —
        verified 2026-07-22 that nothing else consumes it.

    Returns
    -------
    list of dict
        The validated task mappings, in document order.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        If the store is structurally invalid: ``tasks`` is not a list, a
        task is missing ``id`` or ``title``, an ``id`` is duplicated, a
        ``status`` is not in :data:`VALID_STATUSES`, or a ``priority`` is
        present but not an integer.

    Examples
    --------
    >>> tasks = load_tasks()  # doctest: +SKIP
    >>> tasks[0]["id"]                     # doctest: +SKIP
    'design'
    """
    data = load_doc(path, validate=True)
    return data.get("tasks")


def load_doc(path: str | Path, *, validate: bool = False) -> dict:
    """Load the FULL store document from the database.

    The single-read primitive that both :func:`load_tasks` and the ``_store``
    CRUD verbs build on. Returning the *whole* top-level mapping (not just
    ``tasks``) lets a read-modify-write cycle reuse one read for BOTH the
    ``tasks`` payload it mutates AND the non-``tasks`` sections (notably the
    ``users:`` registry) it must carry through untouched.

    Parameters
    ----------
    path : str or pathlib.Path
        The logical store identity. ACCEPTED AND IGNORED: the database is
        resolved from the environment, and since the validation label started
        naming the database actually read, this value reaches no error text
        either. It survives as the signature every CRUD verb passes through —
        verified 2026-07-22 that nothing else consumes it.
    validate : bool, default False
        When True, run :func:`_validate_tasks` on ``data.get("tasks")``
        before returning (the read-time gate :func:`load_tasks` applies).

    Returns
    -------
    dict
        The store document.

    Raises
    ------
    RuntimeError
        If the database is missing, or its read returns no usable document.
        It RAISES rather than returning ``{}``, and that is load-bearing: an
        empty document flows into a read-modify-write and is written back as
        the whole store, which is how 2,138 cards once became one. Emptiness
        must be read, never inferred.
    TaskValidationError
        Only when ``validate=True`` and the ``tasks`` payload is invalid.
    """
    path = Path(path).expanduser()

    # DB-CANONICAL: read the doc FROM SQLITE, not from a file that no longer
    # exists. This is not an optimisation — it is what makes the mode safe.
    #
    # WITHOUT IT, EVERY WRITE ERASES THE BOARD, and the mechanism is worth
    # spelling out because it is silent and total. The CRUD verbs are
    # read-modify-write: they call this function, mutate `doc["tasks"]`, and
    # hand the whole doc to the writer. If this still read the (absent) YAML it
    # would return `{}`, so the "modify" step would build a document holding
    # ONLY the new card, and `mirror_doc_incremental` — which diffs the doc
    # against the DB and deletes what is missing — would remove every other
    # card. Measured on a scratch store during the cutover: writing a second
    # card left exactly one row. On the live board that is 2065 cards down to 1,
    # with no error raised anywhere.
    # `or {}` on this read would be a total-loss hazard: whatever this returns
    # feeds a read-modify-write, so an empty dict is not "no cards" but "write
    # nothing over everything". Delegated to the one fail-loud reader so every
    # caller shares a single policy — one sibling expression being fixed and
    # another not is exactly how that survived the last time.
    from ._store import _read_canonical_db_or_raise

    data = _read_canonical_db_or_raise()
    if validate:
        # NAME THE DATABASE THAT WAS ACTUALLY READ, not ``path``. ``path`` is
        # the caller's logical store name and is still a ``tasks.yaml`` in most
        # call sites, so labelling it ``<sqlite:...>`` produced
        # ``<sqlite:/home/agent/.scitex/cards/tasks.yaml>`` — a file that does
        # not exist, welded to the identifier of the backend that did not read
        # it. Every validation warning carried it, so anyone debugging a
        # resolution problem was handed a path to chase that was never opened.
        from ._db import resolve_db_path

        _validate_tasks(
            data.get("tasks"),
            source=f"<sqlite:{resolve_db_path(None)}>",
            strict=False,
        )
    return data


# ---------------------------------------------------------------------------
# Re-exports. `_model` was 1,235 lines — 2.4x the 512 cap — and therefore could
# not be edited AT ALL, which blocked a P0 fix for a blank board. It is now a
# thin orchestrator over four focused modules (GITIGNORED/REFACTORING.md).
#
# THE IMPORT SURFACE DOES NOT MOVE. 43 test files and every fleet agent do
# `from scitex_cards._model import ...`; every name below is the SAME object it
# always was, defined next door. Same contract as the `_store_write` split (#391).
# ---------------------------------------------------------------------------
from ._deadlines import (  # noqa: E402,F401
    Repeater,
    _add_period,
    _as_aware_utc,
    _get_repeater_rx,
    _last_day_of_month,
    _parse_deadline_or_raise,
    _parse_iso_date_or_raise,
    _pick_next_dt,
    is_overdue,
    next_deadline_for_task,
)
from ._task import (  # noqa: E402,F401
    _BLOCKER_ALIASES,
    ABOLISHED_STATUSES,
    VALID_BLOCKERS,
    VALID_KINDS,
    StaleStoreError,
    StoreShrinkRefusedError,
    Task,
)
from ._validate import (  # noqa: E402,F401
    _warn_tolerated,
)

# ---------------------------------------------------------------------------
# `_store_write` re-exports — LAZY, and the laziness is load-bearing.
#
# `_store_write` imports FROM `_model` (StaleStoreError, load_doc, ...), so an
# eager `from ._store_write import ...` here closes an import CYCLE that only
# survives in ONE direction:
#
#   import scitex_cards._model         -> _model runs to here, pulls in
#                                        _store_write, which imports _model back
#                                        — already in sys.modules with everything
#                                        above this line bound. FINE.
#
#   import scitex_cards._store_write   -> _store_write runs to its line 42, pulls
#                                        in _model, which reaches HERE and asks
#                                        _store_write for names it has not defined
#                                        yet (it is only 42 lines in).
#                                        ImportError. NOT FINE.
#
# So the package worked only because nothing ever imported `_store_write` first
# — every real path reaches it through `_model` or the public API. That is luck,
# not design, and it broke a live data-repair script on 2026-07-14: an external
# caller whose FIRST scitex_cards import was `from scitex_cards._store_write import
# edit_tasks` got an ImportError blaming a circular import rather than their call.
#
# PEP 562 module __getattr__ defers the import to first ATTRIBUTE ACCESS, by
# which time both modules are fully initialised. `from scitex_cards._model import
# save_tasks` still works — `from X import Y` falls back to X.__getattr__.
#
# DO NOT "simplify" this back to a top-level import. The cycle is real; this is
# what breaks it. tests/scitex_cards/test__import_order.py imports each module
# first IN A SUBPROCESS and fails if any order raises.
# ---------------------------------------------------------------------------
_STORE_WRITE_EXPORTS = frozenset(
    {
        "_git_autocommit_store",
        "_save_doc_unlocked",
        "_save_tasks_unlocked",
        "_store_lock",
        "edit_tasks",
        "save_tasks",
        "store_generation",
    }
)


def __getattr__(name: str):
    """Resolve the `_store_write` re-exports on first access (PEP 562)."""
    if name in _STORE_WRITE_EXPORTS:
        from . import _store_write

        value = getattr(_store_write, name)
        globals()[name] = value  # cache: __getattr__ runs once per name
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted([*globals(), *_STORE_WRITE_EXPORTS])
