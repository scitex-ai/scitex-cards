#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write path of the user registry — register / alias / notify / heartbeat.

Split out of ``_store.py`` (line-budget refactor, 2026-07-18) together with
``_store_read.py``; ``_store.py`` remains the thin orchestrator re-exporting
the public API. Every mutation here re-reads the ``users:`` section through
the UNCACHED :func:`~._store_read._load_users_section` under the shared store
lock — the read cache in ``_store_read`` is for lock-free readers only and
must never serve a read-modify-write cycle.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from .._model import _store_lock
from ._model import User, UserValidationError, validate_user
from ._registry_home import _read_users, _write_users
from ._store_read import _resolved_store

#: Stable user-id prefix. See package docstring for the full format.
_USER_ID_PREFIX = "u_"

#: Number of hex chars in the random token portion of a user id (48 bits).
_USER_ID_TOKEN_HEX = 12

#: The EXACT shape every user id has — ``u_`` + 12 lowercase hex chars —
#: whether minted here (:func:`_generate_user_id` via ``secrets.token_hex``,
#: which emits lowercase hex) or supplied by the caller. A caller-supplied id
#: is validated against this pattern so the registry never holds an id the
#: random path could not have produced.
_USER_ID_RE = re.compile(
    rf"^{_USER_ID_PREFIX}[0-9a-f]{{{_USER_ID_TOKEN_HEX}}}$"
)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with the canonical ``Z`` suffix.

    Identical second-resolution shape to ``_store._utc_now_iso`` so user and
    task timestamps are consistent on disk; re-implemented locally to keep
    this package free of an import cycle with the task mutation layer.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _save_users_unlocked(users: list[dict], path: Path) -> None:
    """Write the ``users:`` section, preserving the ``tasks:`` payload.

    *** DO NOT restore the ruamel round-trip here. IT COST ~46–171 s PER CARD
    WRITE. *** This is the liveness-HEARTBEAT write: every ``update_task`` /
    ``add_task`` / ``comment`` by a registered agent stamps ``last_seen`` and
    lands here. The old body used a ruamel round-trip loader+dumper to preserve
    the ``tasks:`` list's inline comments and key order — but ``_store_write.
    _save_doc_unlocked`` (the TASK write path) already abandoned comment
    preservation ("the store is machine-managed, so dropping those comments is
    accepted") and writes with the fast safe loader/dumper. So this path was
    ruamel-round-tripping the ENTIRE 6.5 MB store to preserve comments the task
    path had ALREADY dropped — pure cost, on every write.

    MEASURED 2026-07-15 on the live store (actor registered → heartbeat fires):
    0.9.4 = 46 s/write, 0.13.x = 171 s/write. Root cause of the board's
    per-write latency and today's write timeouts.

    Now uses the SAME fast safe path as the task write: ``safe_load`` the doc,
    replace only ``users:``, ``safe_dump``, and keep the identical crash-safety
    (atomic tmp + fsync + reparse-verify + ``os.replace``). Comment-preservation
    is intentionally gone — it was already gone one module over.

    Direct callers MUST already hold ``_store_lock(path)``.
    """
    import os

    from .._yaml import safe_dump, safe_load

    for row in users:
        validate_user(row)

    doc: dict = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = safe_load(handle)
        if isinstance(loaded, dict):
            doc = loaded
    doc["users"] = users
    # Keep the document valid for ``_model.load_tasks`` even when this is a
    # users-FIRST write (no task ever added yet): that loader hard-requires a
    # top-level ``tasks:`` list, so a file carrying only ``users:`` would make
    # a later ``add_task`` (which calls ``load_tasks`` on the existing file)
    # fail-loud. Seed an empty ``tasks:`` list when absent; never touch an
    # existing one.
    if not isinstance(doc.get("tasks"), list):
        doc["tasks"] = []

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        dumped = safe_dump(doc)  # serialize to a string once (stream=None)
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(dumped)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        # Reparse-verify the tmp file before promoting it — never replace the
        # canonical SSOT with bytes that don't round-trip. Fast safe loader.
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = safe_load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did "
                f"not reparse cleanly after dump "
                f"({type(verify_exc).__name__}: {verify_exc}). Canonical "
                f"file left untouched."
            ) from verify_exc
        verify_users = verify_doc.get("users") if isinstance(verify_doc, dict) else None
        if not isinstance(verify_users, list) or len(verify_users) != len(users):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected users payload. Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _generate_user_id(existing_ids: set[str]) -> str:
    """Generate a fresh stable id not present in ``existing_ids``.

    Re-rolls on the vanishingly rare clash with a currently-registered id.
    Ids of REMOVED users are not tracked here; the 48-bit random token
    makes accidental reuse of a past id astronomically unlikely.
    """
    while True:
        uid = _USER_ID_PREFIX + secrets.token_hex(_USER_ID_TOKEN_HEX // 2)
        if uid not in existing_ids:
            return uid


def deterministic_user_id(issuer: str, subject: str) -> str:
    """Derive the canonical DETERMINISTIC user id for an OIDC identity.

    THE single source of the derivation, fleet-wide (decision card
    ``cards-email-uniqueness-is-fleet-wide-not-per-host-20260814``,
    2026-08-14): user ids minted independently per host give the same human
    a DIFFERENT ``u_*`` id on every host, and the cross-host sync's
    do-nothing-on-conflict rule then silently splits that human's identity
    at reconcile. Deriving the id from the OIDC ``(issuer, subject)`` pair
    makes the same identity converge to the SAME id on every host with zero
    coordination. scitex-hub calls THIS function instead of keeping a copy
    — two implementations of "hash the pair" that disagree on framing would
    mint different ids for the same human, which is the exact bug this
    function exists to delete.

    The payload framing is length-prefixed and NON-NEGOTIABLE::

        f"{len(issuer)}:{issuer}|{len(subject)}:{subject}"

    A naive ``f"{issuer}|{subject}"`` is ambiguous — issuer ``"https://x"``
    with subject ``"a|b"`` and issuer ``"https://x|a"`` with subject ``"b"``
    both frame to ``"https://x|a|b"``, i.e. two different people silently
    sharing one board identity (found by scitex-hub, same card; subjects
    are provider-controlled opaque strings, so this is a real input
    surface). The length prefixes make the framing injective by
    construction; a test guards it against being "simplified" back.

    The framed payload is UTF-8 encoded, SHA-256 hashed, and the hex digest
    truncated to the exact id format :func:`register_user` accepts
    (``u_`` + 12 lowercase hex chars), so the result is always a valid
    caller-supplied ``id`` for :func:`register_user`.

    One human with SEVERAL providers: the id derives from the identity that
    claimed the verified email ON THIS HOST — a later provider ATTACHES to
    the existing user via the explicit link flow and never mints.
    Cross-provider convergence is the link flow's job, never this
    function's (same card, settled 2026-08-14).

    Raises
    ------
    UserValidationError
        If ``issuer`` or ``subject`` is not a non-empty string.
    """
    import hashlib

    if not (isinstance(issuer, str) and issuer):
        raise UserValidationError(
            f"deterministic_user_id: issuer must be a non-empty string "
            f"(got {issuer!r})"
        )
    if not (isinstance(subject, str) and subject):
        raise UserValidationError(
            f"deterministic_user_id: subject must be a non-empty string "
            f"(got {subject!r})"
        )
    payload = f"{len(issuer)}:{issuer}|{len(subject)}:{subject}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return _USER_ID_PREFIX + digest[:_USER_ID_TOKEN_HEX]


def _names_index(users: list[dict]) -> dict[str, str]:
    """Map every registered name → its owning user id (uniqueness checks)."""
    index: dict[str, str] = {}
    for u in users:
        for name in u.get("names") or []:
            index[name] = u.get("id")
    return index


# --------------------------------------------------------------------------- #
# Public write API                                                            #
# --------------------------------------------------------------------------- #
def register_user(
    *,
    kind: str,
    names: "list[str] | str",
    id: str | None = None,
    host_at_name: str | None = None,
    notify: dict | None = None,
    turn_url: str | None = None,
    a2a_port: int | None = None,
    store: str | Path | None = None,
) -> User:
    """Register a new user — freshly generated stable id, or caller-supplied.

    Validates the record, rejects any name that already maps to an existing
    user (names are UNIQUE across the registry — fail-loud), then appends
    and persists atomically under the shared store lock. Returns the
    created :class:`User`.

    ``id`` (optional, added 2026-08-14) exists for DETERMINISTIC fleet-wide
    identity — decision card
    ``cards-email-uniqueness-is-fleet-wide-not-per-host-20260814``: with the
    store synchronised across hosts, ids minted randomly per host give the
    same human a different ``u_*`` id on every host, and the sync's
    do-nothing-on-conflict rule silently splits that identity at reconcile.
    A caller (scitex-hub) that derives the id from the OIDC identity — via
    :func:`deterministic_user_id`, the canonical derivation — makes the same
    human converge to the SAME id on every host with zero coordination.
    Three-valued honesty when ``id`` is supplied:

    - CREATED: the id is unused → registered exactly like a random mint.
    - ALREADY-EXISTED-SAME: the id belongs to a user with the same ``kind``
      that already carries EVERY requested name → idempotent re-registration;
      the existing record is returned verbatim, nothing is written (config
      fields passed here are NOT applied — use :func:`set_notify` /
      :func:`add_alias` to change an existing user).
    - REFUSED-DIFFERENT: the id belongs to any OTHER identity (kind
      mismatch, or a requested name the existing user does not carry) →
      fail-loud :class:`UserValidationError`, mirroring the existing
      name-collision path. NEVER silently adopts or merges.

    Parameters
    ----------
    kind : str
        One of :data:`VALID_USER_KINDS`.
    names : list[str] | str
        One or more display-name aliases (a bare string is accepted as a
        single-element list). At least one non-empty string.
    id : str, optional
        Caller-supplied stable id. Must match the EXACT format the random
        path mints — ``u_`` + 12 lowercase hex chars (fail-loud
        :class:`UserValidationError` otherwise, naming the expected
        format). Default ``None`` preserves the previous behavior
        byte-for-byte: a fresh random id via :func:`_generate_user_id`.
    host_at_name : str, optional
        Optional canonical ``host@name`` join key.
    notify : dict, optional
        Opaque notify-config bag (stored verbatim; default ``{}``).
    turn_url : str, optional
        Optional explicit delivery endpoint (the agent's HTTP turn URL).
        Validated as a non-empty string when present. See
        :func:`scitex_cards._users.user_turn_url` for how it is consumed.
    a2a_port : int, optional
        Optional a2a listen port; when set (and no ``turn_url``) the turn
        URL is derived as ``http://<host>:<a2a_port>/v1/turn``. Validated
        as a positive int when present.
    store : str | Path, optional
        Store path override (default: the resolved task store).

    Raises
    ------
    UserValidationError
        On any structural fault, if a provided name is already taken by
        another user, if a supplied ``id`` is malformed, or if a supplied
        ``id`` already belongs to a different identity.
    """
    if isinstance(names, str):
        names = [names]
    names = list(names or [])
    if id is not None and not (isinstance(id, str) and _USER_ID_RE.fullmatch(id)):
        raise UserValidationError(
            f"cannot register user: invalid caller-supplied id {id!r}; "
            f"expected {_USER_ID_PREFIX!r} followed by exactly "
            f"{_USER_ID_TOKEN_HEX} lowercase hex chars (e.g. 'u_3f9a1c0b7e42')"
        )
    path = _resolved_store(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        users = _read_users(store, path)
        existing_ids = {u.get("id") for u in users if u.get("id")}
        name_owner = _names_index(users)
        if id is not None and id in existing_ids:
            existing = next(u for u in users if u.get("id") == id)
            existing_names = list(existing.get("names") or [])
            same_identity = (
                existing.get("kind") == kind
                and bool(names)
                and all(n in existing_names for n in names)
            )
            if same_identity:
                # Idempotent re-registration of the SAME identity: return
                # the existing record verbatim, no write.
                return User.from_dict(existing)
            raise UserValidationError(
                f"cannot register user: id {id!r} already belongs to a "
                f"different identity (existing kind="
                f"{existing.get('kind')!r}, names={existing_names!r}; "
                f"requested kind={kind!r}, names={names!r}); refusing to "
                f"adopt or merge an existing user — attach new names via "
                f"add_alias instead"
            )
        for name in names:
            if name in name_owner:
                raise UserValidationError(
                    f"cannot register user: name {name!r} already belongs "
                    f"to user {name_owner[name]!r}"
                )
        new = User(
            id=id if id is not None else _generate_user_id(existing_ids),  # type: ignore[arg-type]
            kind=kind,
            names=names,
            host_at_name=host_at_name,
            notify=dict(notify) if notify else {},
            turn_url=turn_url,
            a2a_port=a2a_port,
            created_at=_utc_now_iso(),
        )
        validate_user(new)
        users.append(new.to_dict())
        _write_users(users, store, path)
    return new


def add_alias(
    user_id: str,
    name: str,
    store: str | Path | None = None,
) -> User:
    """Add ``name`` to ``user_id``'s ``names`` list (idempotent).

    No-op (returns the user unchanged) when the name is already one of the
    user's aliases. Rejects (fail-loud) a name that already belongs to a
    DIFFERENT user — names stay unique across the registry. Returns the
    updated :class:`User`.

    Raises
    ------
    UserValidationError
        If ``name`` is empty, ``user_id`` is unknown, or ``name`` already
        belongs to another user.
    """
    if not (isinstance(name, str) and name):
        raise UserValidationError(
            f"add_alias: name must be a non-empty string (got {name!r})"
        )
    path = _resolved_store(store)
    with _store_lock(path):
        users = _read_users(store, path)
        name_owner = _names_index(users)
        owner = name_owner.get(name)
        if owner is not None and owner != user_id:
            raise UserValidationError(
                f"cannot add alias {name!r} to user {user_id!r}: it already "
                f"belongs to user {owner!r}"
            )
        target = next((u for u in users if u.get("id") == user_id), None)
        if target is None:
            raise UserValidationError(f"add_alias: unknown user id {user_id!r}")
        current = list(target.get("names") or [])
        if name not in current:
            current.append(name)
            target["names"] = current
            validate_user(target)
            _write_users(users, store, path)
        return User.from_dict(target)


def set_notify(
    user_id: str,
    notify: dict,
    store: str | Path | None = None,
) -> User:
    """Replace ``user_id``'s opaque ``notify`` dict. Returns the updated user.

    The contents are stored verbatim (not interpreted here). Raises
    :class:`UserValidationError` if ``user_id`` is unknown or ``notify`` is
    not a mapping.
    """
    if not isinstance(notify, dict):
        raise UserValidationError(
            f"set_notify: notify must be a mapping (got {notify!r})"
        )
    path = _resolved_store(store)
    with _store_lock(path):
        users = _read_users(store, path)
        target = next((u for u in users if u.get("id") == user_id), None)
        if target is None:
            raise UserValidationError(f"set_notify: unknown user id {user_id!r}")
        target["notify"] = dict(notify)
        validate_user(target)
        _write_users(users, store, path)
        return User.from_dict(target)


def touch_user(
    name_or_id: str,
    store: str | Path | None = None,
) -> "User | None":
    """Stamp ``last_seen = now(UTC)`` on the acting agent's registry record.

    The heartbeat write. ``name_or_id`` is resolved via the SAME identity
    seam as everything else (:func:`~._store_read.resolve_user`, then an
    exact id match), so there is NO second identity path. Returns the updated
    :class:`User`, or ``None`` when the actor maps to no registered user (an
    UNREGISTERED actor has no record to stamp — the caller decides whether
    that is tolerable; the heartbeat itself never raises for that case).

    This is scitex-cards's OWN liveness signal — a local write to the local
    registry, NEVER an external-runtime probe.
    """
    if not (isinstance(name_or_id, str) and name_or_id.strip()):
        return None
    key = name_or_id.strip()
    path = _resolved_store(store)
    with _store_lock(path):
        users = _read_users(store, path)
        target = None
        # Resolution order mirrors resolve_user: exact id → name alias →
        # host_at_name join key. One identity seam, no second path.
        for u in users:
            if u.get("id") == key or key in (u.get("names") or []):
                target = u
                break
        if target is None:
            for u in users:
                if u.get("host_at_name") and u.get("host_at_name") == key:
                    target = u
                    break
        if target is None:
            return None
        target["last_seen"] = _utc_now_iso()
        validate_user(target)
        _write_users(users, store, path)
        return User.from_dict(target)


__all__ = [
    "add_alias",
    "deterministic_user_id",
    "register_user",
    "set_notify",
    "touch_user",
]

# EOF
