#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + loader/validator/writer for scitex-cards.

The task store is the database; this module models it as a
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


def load_tasks(path: str | Path, *, tolerant: bool = False) -> list[dict]:
    """Load and validate the task list from the store.

    Parameters
    ----------
    path : str or pathlib.Path
        Names which logical store is addressed; used in error text only.

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
    data = load_doc(path, validate=True, tolerant=tolerant)
    return data.get("tasks")


def load_doc(
    path: str | Path, *, validate: bool = False, tolerant: bool = False
) -> dict:
    """Load the FULL store document from the database.

    The single-read primitive that both :func:`load_tasks` and the ``_store``
    CRUD verbs build on. Returning the *whole* top-level mapping (not just
    ``tasks``) lets a read-modify-write cycle reuse one read for BOTH the
    ``tasks`` payload it mutates AND the non-``tasks`` sections (notably the
    ``users:`` registry) it must carry through untouched.

    Parameters
    ----------
    path : str or pathlib.Path
        Names which logical store is addressed; used in error text only.
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

    # DB-CANONICAL: read the doc FROM THE DATABASE, not from a file that no longer
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
    # `tolerant` is for callers that NEVER write the document back. The
    # comment above is exactly why it must not become the default: the
    # read-modify-write verbs hand this doc to the writer, and a row omitted
    # here would be DELETED there. See `_store_tolerant_read` for the door,
    # and `test__rmw_refusal_must_not_become_tolerance.py` for the guard that
    # fails if the mutate path ever acquires this behaviour.
    if tolerant:
        from ._store_tolerant_read import read_doc_tolerating_unreadable_rows

        data = read_doc_tolerating_unreadable_rows()
    else:
        from ._store import _read_canonical_db_or_raise

        data = _read_canonical_db_or_raise()
    if validate:
        _validate_tasks(
            data.get("tasks"),
            source=_canonical_source_label(),
            strict=False,
        )
    return data


def _canonical_source_label() -> str:
    """Name the store the rows ACTUALLY came from.

    This label hardcoded an engine name until 2026-08-02, and it was wrong in
    two independent ways at once:

    1. The engine was HARDCODED, so every tolerated-validation warning named
       the wrong one whenever the canonical store was a server.
    2. ``path`` is the YAML argument — and the comment block a few lines above
       states in its own words that the doc is read "not from a file that no
       longer exists". So the label pointed at a file this very function
       documents as absent.

    The rows come from :func:`_read_canonical_db_or_raise`, i.e. from the
    canonical store. Naming anything else is not a cosmetic slip: during an
    incident it sends the reader to a backend and a location that have nothing
    to do with the failure, and they will spend real time there before
    suspecting the label. Reported by scitex-app, who hit exactly that.

    A DSN is rendered WITHOUT its query string and never through ``Path``.
    ``Path`` collapses ``//`` to ``/``, which is what turned
    ``postgresql://host/db`` into ``postgresql:/host/db`` elsewhere in this
    package and had two agents reporting a malformed-URL bug against a config
    that was correct.
    """
    from ._store_target import resolve_store_target  # noqa: PLC0415
    from ._store_url import is_postgres_url  # noqa: PLC0415

    try:
        target = resolve_store_target(None)
    except Exception:  # noqa: BLE001 -- a label must never break the read
        return "<store:unresolved>"
    if is_postgres_url(target):
        # Strip any query string: DSNs carry credentials there, and this label
        # is written into warnings that land in logs.
        return f"<postgres:{str(target).split('?', 1)[0]}>"
    return f"<store:{target}>"


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
    WRITE_SOURCE,
    _side_of,
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
