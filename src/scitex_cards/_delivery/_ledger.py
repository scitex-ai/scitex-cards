#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The delivery ledger — a KEYED DEDUP MAP, the sole source of delivery truth.

This is NOT an append-log. It is a map keyed by ``(recipient,
notification_id, channel)`` whose value records the latest delivery state
for that exact tuple::

    {recipient}\\x1f{notification_id}\\x1f{channel}:
      status: sent | failed
      attempts: 3
      last_ts: 2026-06-27T10:00:00Z
      next_eligible_ts: 2026-06-27T10:00:30Z   # failures only

Persisted as JSON at ``<store_dir>/runtime/delivery_ledger.json`` where
``<store_dir>`` is the parent directory of the resolved task store
(:func:`scitex_cards._inbox._resolved_store`). This is regenerable runtime
state, so an absent ledger simply starts empty (no legacy migration). Writes
are ATOMIC (temp file + ``os.replace``) and serialised behind the same advisory
``flock`` the task store uses (:func:`scitex_cards._model._store_lock`), keyed
on the LEDGER path — single-writer per ledger file.

The ledger is what makes delivery idempotent: it answers "have we already
sent this?" (:meth:`Ledger.already_done`) and "is this failed item due for
another try?" (:meth:`Ledger.retry_eligible`) WITHOUT ever touching the
user's inbox ``seen`` cursor (that is the user's read state, separate).
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from .._inbox import _resolved_store
from .._model import _store_lock
from ._channel import DeliveryResult, Status

#: Ledger filename, under the store's ``runtime/`` dir.
LEDGER_FILENAME = "delivery_ledger.json"

#: Max delivery attempts before a failed item is left terminal (no retry).
MAX_ATTEMPTS = 5

#: Ledger-only status for a failure whose retry budget is exhausted. NOT a
#: channel result (channels only ever report sent/failed/skipped); the ledger
#: promotes a ``failed`` entry to this once ``attempts >= MAX_ATTEMPTS`` so a
#: permanently-undeliverable notification is a VISIBLE comm-miss marker, not a
#: silently-dropped item. The loop surfaces this loudly exactly once.
TERMINAL_STATUS = "failed_terminal"

#: Base backoff in seconds; doubles per attempt, capped at MAX_BACKOFF_SEC.
BASE_BACKOFF_SEC = 5

#: Upper bound on the exponential backoff window.
MAX_BACKOFF_SEC = 3600

#: Field separator inside the flat composite ledger key. ASCII Unit
#: Separator (0x1f) — never appears in a recipient id / notification id /
#: channel name, so the key round-trips through YAML unambiguously.
_KEY_SEP = "\x1f"


def _utc_now() -> _dt.datetime:
    """Timezone-aware UTC now (the default ``now`` for ledger ops)."""
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(ts: _dt.datetime) -> str:
    """Render a datetime as canonical ``Z``-suffixed ISO-8601 (sec res)."""
    return (
        ts.astimezone(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_iso(value: str | None) -> _dt.datetime | None:
    """Parse a stored ISO timestamp back to an aware datetime (None-safe)."""
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def ledger_path(store: str | Path | None = None) -> Path:
    """Resolve the ledger path: ``<store_dir>/runtime/delivery_ledger.json``.

    Lives under the store's ``runtime/`` dir (scitex convention for
    non-git-tracked runtime state), under whichever scope the store resolved to.
    """
    from .._paths import runtime_dir

    return runtime_dir(store) / LEDGER_FILENAME


def _make_key(recipient: str, note_id: str, channel: str) -> str:
    """Build the flat composite ledger key for one delivery tuple."""
    return f"{recipient}{_KEY_SEP}{note_id}{_KEY_SEP}{channel}"


class Ledger:
    """In-memory view of the delivery ledger, loaded from + saved to disk.

    Construct via :meth:`load` (reads the YAML once), query with
    :meth:`already_done` / :meth:`retry_eligible`, mutate with
    :meth:`record` (which persists atomically under the lock). A single
    delivery run loads the ledger once, records each outcome, and the file
    is rewritten on every ``record`` — small files, single-writer, atomic.
    """

    def __init__(self, path: Path, entries: dict[str, dict]):
        self._path = path
        #: key -> {status, attempts, last_ts, next_eligible_ts}
        self._entries: dict[str, dict] = entries

    # ------------------------------------------------------------------ #
    # Construction                                                        #
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, store: str | Path | None = None) -> "Ledger":
        """Load the ledger map off disk (absent/malformed → empty)."""
        path = ledger_path(store)
        entries = cls._load_entries(path)
        return cls(path, entries)

    @staticmethod
    def _load_entries(path: Path) -> dict[str, dict]:
        """Read the raw JSON ledger mapping (defensive: bad shapes → {}).

        This is regenerable runtime state (a delivery-dedup ledger under
        ``runtime/``), so there is no legacy migration: an absent/unreadable
        ledger simply starts empty and is rebuilt on the next delivery run.
        """
        import json

        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            data: object = json.loads(text) if text.strip() else {}
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict] = {}
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, dict):
                out[key] = dict(value)
        return out

    # ------------------------------------------------------------------ #
    # Queries                                                             #
    # ------------------------------------------------------------------ #
    def _get(self, recipient: str, note_id: str, channel: str) -> dict | None:
        return self._entries.get(_make_key(recipient, note_id, channel))

    def already_done(self, recipient: str, note_id: str, channel: str) -> bool:
        """True iff this tuple was already ``sent`` (terminal success)."""
        entry = self._get(recipient, note_id, channel)
        return bool(entry) and entry.get("status") == Status.SENT.value

    def retry_eligible(
        self,
        recipient: str,
        note_id: str,
        channel: str,
        now: _dt.datetime | None = None,
    ) -> bool:
        """True iff a prior FAILURE is due for another attempt.

        Eligible when: status==failed AND attempts < MAX_ATTEMPTS AND
        ``now >= next_eligible_ts``. A tuple with no entry yet is NOT
        "retry eligible" (it's a first attempt — handled by the loop's
        already_done==False path); this predicate is only about resuming a
        KNOWN failure after its backoff window.
        """
        entry = self._get(recipient, note_id, channel)
        if not entry or entry.get("status") != Status.FAILED.value:
            return False
        if int(entry.get("attempts", 0)) >= MAX_ATTEMPTS:
            return False
        now = now or _utc_now()
        next_ts = _parse_iso(entry.get("next_eligible_ts"))
        if next_ts is None:
            return True
        return now >= next_ts

    def has_failure(self, recipient: str, note_id: str, channel: str) -> bool:
        """True iff a (non-terminal) failure entry already exists for the tuple.

        Only the RETRYABLE ``failed`` status — a ``failed_terminal`` entry is
        NOT a "has_failure" for the loop's backoff check (it's handled by
        :meth:`is_terminal`, which stops re-attempts entirely).
        """
        entry = self._get(recipient, note_id, channel)
        return bool(entry) and entry.get("status") == Status.FAILED.value

    def is_terminal(self, recipient: str, note_id: str, channel: str) -> bool:
        """True iff this tuple FAILED permanently (retry budget exhausted).

        A terminal failure is recorded once (when ``attempts`` first reaches
        ``MAX_ATTEMPTS``), surfaced loudly by the loop that one time, then left
        as a persistent comm-miss marker — never retried, never re-warned. The
        operator must intervene (fix the channel / address) to clear it.
        """
        entry = self._get(recipient, note_id, channel)
        return bool(entry) and entry.get("status") == TERMINAL_STATUS

    # ------------------------------------------------------------------ #
    # Mutation                                                            #
    # ------------------------------------------------------------------ #
    def _backoff_seconds(self, attempts: int, retry_after: float | None = None) -> int:
        """Backoff in seconds for a failed attempt, capped at MAX_BACKOFF_SEC.

        When ``retry_after`` is a positive hint (a 429 ``Retry-After``), HONOR
        it — the server told us exactly how long to wait, so we obey that
        rather than guessing with the exponential default. When it is ``None``
        (or non-positive), fall back to the exponential schedule
        ``BASE * 2**(attempts-1)``. Either way the result is clamped to
        ``MAX_BACKOFF_SEC`` and the attempt still counts toward MAX_ATTEMPTS.
        """
        if retry_after is not None and retry_after > 0:
            return min(int(retry_after), MAX_BACKOFF_SEC)
        if attempts <= 0:
            attempts = 1
        delay = BASE_BACKOFF_SEC * (2 ** (attempts - 1))
        return min(delay, MAX_BACKOFF_SEC)

    def record(
        self,
        recipient: str,
        note_id: str,
        channel: str,
        result: DeliveryResult,
        now: _dt.datetime | None = None,
    ) -> dict:
        """Record ``result`` for the tuple and persist atomically.

        Increments ``attempts``, stamps ``last_ts``, sets ``status`` from
        the result, and — for a FAILURE — computes the next backoff
        ``next_eligible_ts`` (honoring ``result.retry_after`` when set, else
        the exponential default). A ``sent`` clears any backoff (it's
        terminal). A ``skipped`` is NON-terminal policy feedback: it stamps
        the entry but does NOT bump ``attempts`` and does NOT become a
        failure, so the item is freely re-evaluated next run.

        Returns the updated entry dict (a copy).
        """
        now = now or _utc_now()
        key = _make_key(recipient, note_id, channel)
        entry = dict(self._entries.get(key) or {})
        status = result.status

        if status == Status.SKIPPED:
            # Policy gate said "not now" — record state without consuming a
            # retry budget. Keep prior attempts; do not set a backoff.
            # INTENTIONAL: this clears any prior failure's next_eligible_ts, so
            # a failed item that becomes retry-due then gets quiet-hours-skipped
            # resets its backoff clock but KEEPS attempts — MAX_ATTEMPTS still
            # bounds it, so there is no infinite retry loop.
            entry["status"] = Status.SKIPPED.value
            entry.setdefault("attempts", int(entry.get("attempts", 0)))
            entry["last_ts"] = _iso(now)
            entry["next_eligible_ts"] = None
        else:
            attempts = int(entry.get("attempts", 0)) + 1
            entry["attempts"] = attempts
            entry["last_ts"] = _iso(now)
            if status == Status.FAILED:
                if attempts >= MAX_ATTEMPTS:
                    # Retry budget exhausted → TERMINAL. Promote to the
                    # ledger-only failed_terminal status so the comm-miss is a
                    # visible, queryable marker; the loop surfaces it loudly
                    # once and never re-attempts it.
                    entry["status"] = TERMINAL_STATUS
                    entry["next_eligible_ts"] = None
                else:
                    entry["status"] = status.value
                    # Honor a server retry hint (429 Retry-After) over the
                    # exponential default; either way still capped + counted.
                    backoff = self._backoff_seconds(
                        attempts, getattr(result, "retry_after", None)
                    )
                    entry["next_eligible_ts"] = _iso(
                        now + _dt.timedelta(seconds=backoff)
                    )
            else:  # SENT — terminal success, no further retry.
                entry["status"] = status.value
                entry["next_eligible_ts"] = None

        if result.detail:
            entry["detail"] = result.detail

        self._entries[key] = entry
        self._save()
        return dict(entry)

    # ------------------------------------------------------------------ #
    # Persistence                                                         #
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        """Write the ledger map atomically under the per-ledger flock.

        temp file + ``os.replace`` so a crash mid-write never leaves a
        half-written ledger; the advisory lock (keyed on the ledger path)
        serialises concurrent writers — single-writer per file.
        """
        import json

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _store_lock(self._path):
            tmp_path = self._path.parent / f".{self._path.name}.tmp"
            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    json.dump(
                        self._entries,
                        handle,
                        sort_keys=True,
                        indent=2,
                        ensure_ascii=False,
                    )
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise


__all__ = [
    "BASE_BACKOFF_SEC",
    "LEDGER_FILENAME",
    "MAX_ATTEMPTS",
    "MAX_BACKOFF_SEC",
    "TERMINAL_STATUS",
    "Ledger",
    "ledger_path",
]

# EOF
