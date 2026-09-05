#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side Python API for the scitex-cards task store.

THIN ORCHESTRATOR. The verbs themselves now live in focused siblings; this
module owns the SHARED helpers they all pull on (identity resolution, the
timestamp, the locked read-modify-write loader) and RE-EXPORTS the whole
public surface, so every historical import keeps working untouched —
``from ._store import add_task``, ``_store.complete_task(...)``,
``from ._store import _resolved_store`` / ``load_tasks``, the MCP tool table,
the CLI, the tests.

    _store_mutate      add_task / update_task (+ `_stamp_deferred_at`)
    _store_lifecycle   complete / resolve / reopen / reassign / delete / restore
    _store_comment     comment_task (the card's Issue-activity log)
    _store_relations   set_edge / set_collaborator / set_subscriber
    _store_events      the fail-soft card-event + unblock emit seams
    _store_list        the READ half: list_tasks / summarize_tasks / _match
    _store_write       the LOW-LEVEL persistence layer (lock / save)

Every mutation runs through :func:`_model.save_tasks`, which holds an
``fcntl.flock``-based mutex on a sibling lock file so two concurrent writers
cannot interleave (PHASE 1 prereq for the cross-host sync substrate — see
``GITIGNORED/ARCHITECTURE.md`` Req 2).

The filter functions are read-only and do NOT lock — they snapshot the
store via :func:`_model.load_tasks` and apply the filter in memory.

Design constraints
------------------
- **Generic** (Req 8): scope/assignee/status are free-form strings. The
  helpers don't know what an "agent" is.
- **Centralized** (Req 3): the default store is the database resolved by
  ``$SCITEX_CARDS_DB``; callers can override with an explicit ``store=``
  target. One board for the fleet covers Req 7.
- **Shared with scopes** (Req 1): ``$SCITEX_CARDS_SCOPE`` provides the
  default value for ``list_tasks(scope=...)`` when the caller doesn't pass
  one explicitly. Pass ``scope=""`` (empty string) to ignore the env
  default and see everything.

C5 (card-event producers) wires the mutating verbs to ALSO emit a
canonical :class:`scitex_cards._events.Event` onto the hook bus, plus a
new atomic :func:`reassign_task` owner-change primitive. The mutation→
event mapping (each emit is ADDITIVE + FAIL-SOFT — the mutation persists
first, THEN we emit, and a raising/slow emit can never break or roll back
the write):

    add_task        → ``created``        {card_id, actor, ts}
    comment_task    → ``commented``      (IN ADDITION to the existing
                                          ``card-message`` dispatch)
    update_task     → ``status_changed`` ONLY when ``status`` actually
                      changes {extra:{from,to}} — a completion via
                      update_task(status="done") emits ``completed``
                      (see the completed-vs-status_changed rule below).
    complete_task   → ``completed``      {card_id, actor, ts}
    resolve_task    → ``status_changed`` {extra:{from,to:done}}
    reassign_task   → ``reassigned``     {extra:{from_owner,to_owner}}

Completed-vs-status_changed rule: a flip to ``done`` is modelled as a
single ``completed`` event (NOT also a ``status_changed``) to avoid
double-firing; every OTHER status flip is a ``status_changed``.

There is intentionally NO consumer wired here — delivery / notify is C4
(a separate card). ``reassign_task`` EMITS the ``reassigned`` event; it
does NOT deliver. NO ``sac`` import: this stays a pure producer that
reuses :func:`scitex_cards._events.emit` + the store's own lock/save
helpers.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from ._model import (  # noqa: F401  (see the re-export note below)
    VALID_STATUSES,
    StoreShrinkRefusedError,
    TaskValidationError,
    _save_doc_unlocked,
    _save_tasks_unlocked,
    _store_lock,
    load_doc,
    load_tasks,
    save_tasks,
)
from ._paths import resolve_tasks_path  # noqa: F401  (re-export)

# ^ `VALID_STATUSES`, `load_tasks` and `resolve_tasks_path` are no longer used INSIDE
# this module (they moved out with the read surface) — but they are part of `_store`'s
# de-facto public surface and other modules import them THROUGH it, e.g.
# `_cli/_stale.py`: `from .._store import load_tasks`. "Unused in this file" is not
# "unused". I pruned them once; the full suite caught it immediately. Do not prune
# them again — remove the re-export only together with its importers.
# The READ / QUERY surface now lives in `_store_list` — `list_tasks` /
# `summarize_tasks` / `_match` and the resolvers they need. RE-EXPORTED HERE so
# every existing caller keeps working untouched: `_store.list_tasks(...)`,
# `from ._store import _resolved_store`, the MCP tool table, the CLI, the tests.
#
# The split is not cosmetic. `_store` is the WRITE surface (add / update / complete
# / delete / comment, the locked read-modify-write cycle, enum gating, event
# emission); those two halves share nothing but a store path, and it is the READ
# half the whole fleet hits on every poll — 830 ms per call on the live board,
# every agent, forever. It has earned its own file.
from ._store_list import (  # noqa: F401  (re-export: preserve the public surface)
    ENV_SCOPE,
    _default_scope,
    _match,
    _resolved_store,
    list_tasks,
    summarize_tasks,
)

#: Env var name carrying the agent's identity. Used as the default
#: `completed_by` when :func:`complete_task` doesn't get an explicit `by=`.
ENV_AGENT = "SCITEX_CARDS_AGENT_ID"

#: previous name of :data:`ENV_AGENT`. Renamed 2026-07-02. We fail LOUD (never
#: silently honour it) if it is still set, so a stale export can't quietly
#: mis-attribute a write — the operator must migrate to the new name.
ENV_AGENT_DEPRECATED = "SCITEX_CARDS_AGENT"


def _reject_deprecated_agent_env() -> None:
    """Fail loud if the old ``SCITEX_CARDS_AGENT`` var is still set.

    No silent fallback: a leftover export of the old name is a configuration
    error the operator must fix, not something we quietly translate.
    """
    if os.environ.get(ENV_AGENT_DEPRECATED) is not None:
        raise RuntimeError(
            f"{ENV_AGENT_DEPRECATED} was renamed to {ENV_AGENT}; "
            f"unset the old var (it is no longer honoured)."
        )


class TaskNotFoundError(KeyError):
    """Raised when an update/complete target id is not in the store."""


def _task_not_found(task_id: str) -> TaskNotFoundError:
    """Build the "no such card" error, naming THE STORE THAT WAS SEARCHED.

    ONE BUILDER FOR SEVEN RAISE SITES, because seven copies of a sentence is
    how the wrong one survived this long. Each site used to interpolate its
    own ``tasks_path`` / ``resolved`` local -- the LOCAL sidecar path -- while
    the lookup that had just failed ran against the resolved store. On a
    PostgreSQL deployment the message therefore named a YAML file::

        task id 'x' not found in /home/agent/.scitex/cards/tasks.yaml

    and it named it for a value THAT PLAYED NO PART IN THE SEARCH.
    ``_read_write_doc(path)`` ignores its argument entirely -- its body is
    ``_read_canonical_db_or_raise()``, which takes none -- so that path served
    the file lock and this one string, and nothing else. ``_paths`` already
    said so in prose: "interpolates the path into an error message only".

    That is worse than a vague message, because it is actionable in the WRONG
    DIRECTION: it sent a peer hunting a second store that does not exist, and
    cost them a conclusion they had to retract to another agent.

    ``store_label`` rather than ``resolve_store_target``: the label strips
    credentials before this reaches a log, and never routes a DSN through
    ``Path`` (which collapses ``//`` and mangles it). Calling it here cannot
    fail the caller it is captioning -- reaching this line means the canonical
    read ALREADY SUCCEEDED, so the store is resolvable by construction.
    """
    from ._store_target import store_label

    return TaskNotFoundError(f"task id {task_id!r} not found in {store_label()}")


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _default_agent(arg: str | None) -> str:
    """Resolve an ACTOR/AUTHOR — FAIL LOUD when it cannot be resolved.

    Precedence: an explicit ``by=``/``actor`` arg → ``$SCITEX_CARDS_AGENT_ID``.
    Deliberately does NOT fall back to ``getpass.getuser()`` / ``"unknown"``
    (the former lenient chain): the operator mandate (constitution rule 2
    "fail fast and fail loud, NO silent fallbacks") requires completion /
    comment authorship to record a REAL acting agent, never a blank or
    ``"unknown"`` placeholder that mis-attributes the action on the board.

    Now identical in behaviour to :func:`_resolve_creator_or_raise` — it
    simply delegates to keep a single source of truth (DRY) while preserving
    this public name for the completion/comment callers.

    Raises
    ------
    TaskValidationError
        When the actor resolves to empty or the ``"unknown"`` sentinel, with
        an ACTIONABLE hint naming both fixes.
    """
    return _resolve_creator_or_raise(arg)


def _resolve_creator_or_raise(arg: str | None) -> str:
    """Resolve a card CREATOR — FAIL LOUD when it cannot be resolved.

    Precedence: an explicit ``created_by``/``by=`` arg → ``$SCITEX_CARDS_AGENT_ID``.
    Deliberately does NOT fall back to ``getpass.getuser()`` / ``"unknown"``:
    the operator mandate (constitution rule 2 "fail fast and fail loud, NO
    silent fallbacks") requires a card to record a REAL creator, never a blank
    or ``"unknown"`` placeholder. A card whose creator can't be resolved must
    not be born. This is the SSOT resolver — :func:`_default_agent` (actor /
    author for completion & comments) now delegates here so both share the
    identical fail-loud behaviour.

    Raises
    ------
    RuntimeError
        When the deprecated ``$SCITEX_CARDS_AGENT`` is still exported (renamed
        away — see :func:`_reject_deprecated_agent_env`).
    TaskValidationError
        When the creator resolves to empty or the ``"unknown"`` sentinel,
        with an ACTIONABLE hint naming both fixes.
    """
    _reject_deprecated_agent_env()
    resolved = (arg or os.environ.get(ENV_AGENT) or "").strip()
    if not resolved or resolved == "unknown":
        raise TaskValidationError(
            "creator unresolved — set SCITEX_CARDS_AGENT_ID=<your-agent> or pass "
            "created_by=/by= (creator+assignee are mandatory; no silent "
            "fallback to a blank/'unknown' creator; see constitution)."
        )
    # AN UNEXPANDED PLACEHOLDER IS NOT AN IDENTITY — THE IDENTITY DOOR, WHICH
    # THIS PACKAGE GUARDED EVERYWHERE EXCEPT HERE.
    #
    # `reject_unexpanded_variable` already guards the STORE-TARGET doors
    # (_paths x2, _backend_connect, _db). Identity had none, so until
    # today `_default_agent("${SCITEX_CARDS_AGENT_ID}")` returned that string
    # VERBATIM and it was persisted as an author. Measured 2026-08-21:
    #
    #     '${SCITEX_CARDS_AGENT_ID}'  ACCEPTED -> stored verbatim
    #     '$SCITEX_CARDS_AGENT_ID'    ACCEPTED -> stored verbatim
    #     'unknown'                   REFUSED
    #
    # This is not hypothetical. On 2026-07-18/19 fifteen `tasks` rows were
    # written with a literal `$` in `created_by`; a card closed that incident
    # asserting "0 rows carry the literal env var (was 7)", which was true when
    # written and false afterwards -- a restore brought the rows back and
    # nobody re-measured. The original incident card asked for exactly this
    # guard, in as many words, and it was never built.
    #
    # WHY IT MATTERS NOW RATHER THAN EVENTUALLY: sac injects BOTH the current
    # and the legacy env spellings today, which is the only reason a bad value
    # does not appear. The moment that compatibility path is dropped -- and
    # they are waiting on this guard to drop it -- an agent whose env lacks the
    # CARDS name writes the literal again, silently. So the ordering is
    # dotfiles' spec migration, then THIS, then sac's drop.
    #
    # A blank creator and a placeholder creator are the same defect wearing
    # different clothes: neither names an agent, and the placeholder is worse
    # because it LOOKS resolved on the board.
    # Imported inside the function, matching the existing deferred import of
    # this same helper further down this module — `_store_url` is pulled in
    # lazily here to keep the import graph as it is rather than adding a new
    # module-level edge while fixing an unrelated defect.
    from ._store_url import is_unexpanded_variable

    # THE BRACED HELPER IS NOT ENOUGH HERE, and the gap is deliberate upstream.
    # `is_unexpanded_variable` matches `${FOO}` and NOT bare `$FOO` — a choice
    # that is defensible for STORE TARGETS (a path beginning `$FOO` is odd) and
    # wrong for IDENTITY, where `SCITEX_CARDS_AGENT_ID=$SCITEX_CARDS_AGENT_ID`
    # in a non-expanding context yields exactly the bare form. Measured: the
    # braced form was refused and the bare form sailed through, so the first
    # version of this guard was half a guard.
    #
    # `startswith("$")` is the identity-specific rule and it is deliberately
    # narrow: an agent NAME never begins with a dollar sign, while a dollar
    # elsewhere in a name is nobody's business but the namer's. Verified that
    # `agent-with-$-inside` still resolves, so this rejects the placeholder
    # shape without policing legitimate names.
    if is_unexpanded_variable(resolved) or resolved.startswith("$"):
        raise TaskValidationError(
            f"creator is an UNEXPANDED shell variable, not an agent: {resolved!r}. "
            "Something exported the literal text instead of its value — check the "
            "spec/unit that sets SCITEX_CARDS_AGENT_ID, and whether it is quoted "
            "in a context that never expands it. Writing this would attribute the "
            "card to a placeholder that looks like a real name on the board."
        )
    return resolved


# THE ONE FAIL-LOUD READER now lives in its own module — the guard, the
# incident history behind each of its checks, and its tests belong
# together rather than buried among the mutation helpers. Re-exported
# here so every historical `from ._store import _read_canonical_db_or_raise`
# keeps working, and so there is still exactly ONE reader shared by the
# read door, the write door and `_model.load_doc`.
from ._store_canonical_read import (  # noqa: F401  (re-export)
    _read_canonical_db_or_raise,
)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution and the ``Z`` suffix.

    Trims the microseconds (the operator reads these on the board; second
    resolution is plenty) and uses the canonical ``Z`` suffix rather than
    ``+00:00`` so the string round-trips losslessly through YAML.
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --------------------------------------------------------------------------- #
# Read-modify-write helper                                                     #
# --------------------------------------------------------------------------- #
def _read_write_doc(path: str | Path) -> tuple[dict, list]:
    """Load the FULL store doc ONCE for a locked read-modify-write cycle.

    Returns ``(doc, tasks)`` where ``tasks is doc["tasks"]`` (always a list).
    Callers mutate ``tasks`` (or rebind it) then persist via
    ``_save_doc_unlocked(doc, path, tasks=tasks)``, so this one read serves
    BOTH the mutated ``tasks`` payload AND the non-``tasks`` sections
    (``users:`` etc.) that must survive the rewrite.

    THE ``missing_ok`` PARAMETER IS GONE, and its removal is the safety
    property rather than a tidy-up. It used to mean "an absent store yields an
    empty doc instead of raising", which was reasonable when the store was a
    file that a fresh install legitimately lacked. Against a database it was a
    loaded gun: an empty doc flowed into a read-modify-write, the caller
    appended its one new card, and the mirror diffed that one-card document
    against the DB and deleted every card missing from it.

    Measured on a scratch store during this cutover: five sequential writes
    left exactly ONE row each time. On the live board that is 2065 cards down
    to 1, silently, with nothing raised anywhere in the stack. Found by
    round-tripping real writes, not by reading the diff — the write path looked
    correct in isolation and only end-to-end exercise showed the loss.

    THAT MECHANISM IS CLOSED, and the tense above is deliberate — it describes
    what the removal was FOR, not what would happen today. Two later guards
    would now catch it independently:

      * ``mirror_doc_incremental`` no longer infers a delete from absence at
        all. Its own comment says so, and the ``removed = [i for i in prior if
        i not in now_hashes]`` line it used to end with is gone. Only
        caller-named ``deleted_ids`` are dropped.
      * ``write_doc_to_db``'s shrink guard REFUSES a write missing rows the
        store already has unless ``allow_shrink=True`` — installed after the
        third board wipe.

    ``missing_ok`` STAYS GONE REGARDLESS, and not because of the wipe: an empty
    doc entering a read-modify-write is a bad input on its own terms, whatever
    the layers below do with it. Do not reintroduce it on the grounds that the
    mirror is now safe — that argument reasons from the wrong premise, and the
    guards below are defence in depth rather than permission.

    So there is no "absent store" case to be tolerant about: a missing database
    is a configuration error and :func:`_read_canonical_db_or_raise` says so.
    Emptiness must never be inferred; it must be read.
    """
    doc = _read_canonical_db_or_raise()
    return doc, doc["tasks"]


# --------------------------------------------------------------------------- #
# Public API (kept here)                                                       #
# --------------------------------------------------------------------------- #
def resolve_store(store: str | Path | None = None) -> dict:
    """Return the resolved task store path and the precedence chain.

    Mirrors the data the `scitex-cards resolve-store` CLI verb and the
    `resolve_store` MCP tool emit. Keeping a Python API by the same name
    as the MCP tool satisfies audit §6 (Convention A: tool_name == api_name).

    Output shape::

        {
          "resolved":         "/abs/path/to/cards.db",
          "explicit":         <the `store` arg you passed, or None>,
          "db_env":           <value of $SCITEX_CARDS_DB, or None>,
          "user_store":       "/abs/path/to/~/.scitex/cards/cards.db",
          "pkg_short":        "cards",
          "exists":           bool,
          "store_uuid":       <the database's own identity, or None>,
          "expected_uuid":    <$SCITEX_CARDS_STORE_UUID, or None>,
          "instance_id":      <the SERVER's own identity, or None>,
          "expected_instance": <$SCITEX_CARDS_STORE_INSTANCE, or None>,
          "identity_verdict": "matches" | "differs" | "cannot-tell",
          "identity_reason":  <why, when the verdict is not "matches">,
          "may_proceed":      bool,
        }

    ``store_uuid`` is contract point 8, machine-readable half (design §11). The
    identity is what a host registry must record next to this board's endpoint,
    and "open the database and run a SQL query" is archaeology. This function
    already answers "WHICH store did I actually resolve"; it answers "and WHAT
    IS IT" in the same breath. ``None`` means the database is absent or carries
    no identity yet — bind it with ``scitex-cards store adopt-uuid``.

    ``expected_uuid`` is reported beside it deliberately: the two most useful
    facts about an identity mismatch are the value the database carries and the
    value this process was told to expect, and reading them from two different
    surfaces is how a mismatch stays undiagnosed.

    ``instance_id`` AND WHY ``store_uuid`` WAS NOT ENOUGH. On 2026-08-12 three
    live PostgreSQL databases all answered ``store_uuid =
    1d55dd6e-3d2a-4c24-a429-a78835ab988f`` while holding 3843, 3743 and 3422
    cards. ``store_uuid`` is a ``schema_meta`` ROW and a dump/restore carries
    rows, so every field reported here was byte-identical across stores that
    were hundreds of cards apart — a report that cannot distinguish them is a
    report that confirms whichever one you happened to reach. ``instance_id``
    is the SERVER's own ``system_identifier``, minted by ``initdb`` and present
    in no dump, so it is the one value a copy cannot carry. See
    :mod:`._store_instance` and :mod:`._store_pin`.

    ``may_proceed`` IS THE FIELD TO BRANCH ON, never ``identity_verdict``. A
    caller testing ``verdict != "differs"`` reads "I cannot tell which store
    this is" as a pass, which is exactly how three databases shared one
    identity for five days without anything complaining.

    THIS FUNCTION IS PURE REPORTING — AND THAT INCLUDES THE VERDICT. It reports
    a refusal; it does not perform one. Reading the identity here never mints
    one, never stamps one, and never changes what resolves, and a ``differs``
    verdict raises nothing: this is the verb an operator runs WHEN THINGS ARE
    ALREADY BROKEN, and on 2026-07-31 it was the one verb that CRASHED on the
    case being diagnosed. The enforcing twin is
    :func:`._store_pin.require_pinned_store`, which raises.
    """
    import os

    from ._db import DEFAULT_DB_FILENAME, ENV_DB, resolve_db_path
    from ._paths import PKG_SHORT, _user_root
    from ._store_target import resolve_store_target
    from ._store_url import (
        backend_of,
        is_attempted_dsn,
        is_postgres_url,
        is_unexpanded_variable,
    )
    from ._store_pin import _check_against, instance_at, pinned_instance
    from ._store_uuid import expected_store_uuid, store_uuid_at

    # The resolved store is the DATABASE — the sole store identity. It may be a
    # PATH or a SERVER URL, so it is resolved WITHOUT coercion: this verb exists
    # to answer "which store am I on?", and it was the one verb that CRASHED the
    # moment the answer stopped being a path (measured 2026-07-31, mid-cutover —
    # `resolve-store` raised StoreTargetIsNotAPath against PostgreSQL while
    # `list-tasks` served 2973 cards). A diagnostic that dies on the case you are
    # diagnosing is worse than no diagnostic: it reads as "the store is broken".
    _arg = store if isinstance(store, (str, type(None))) else str(store)
    target = resolve_store_target(_arg)
    on_server = is_postgres_url(target)
    resolved = target if on_server else str(resolve_db_path(_arg))
    # Read ONCE, then used for BOTH the report and the comparison. Two call
    # sites reading the same value independently is how they come to disagree.
    observed_uuid = store_uuid_at(resolved)
    pinned_uuid = expected_store_uuid()
    return {
        "resolved": resolved,
        "explicit": str(store) if store is not None else None,
        "db_env": os.environ.get(ENV_DB),
        "user_store": str(_user_root() / DEFAULT_DB_FILENAME),
        "pkg_short": PKG_SHORT,
        "backend": backend_of(target),
        # THE FIELD THAT WOULD HAVE ENDED THIS IN MINUTES INSTEAD OF DAYS. On
        # 2026-08-12 this verb answered `backend: <a file>, exists: false` for
        # SCITEX_CARDS_DB=":55432" — a port, reported as a file that merely does
        # not exist yet. Both fields were true of the string and neither was
        # true of the intent, so the report read as "fresh install" to every
        # agent who ran it. `backend` cannot carry this: thirteen call sites
        # branch on it two-valued. So the third answer gets its own field, and
        # a diagnosing reader sees the malformation instead of inferring it.
        "target_is_malformed_dsn": is_attempted_dsn(target),
        # THE SIBLING MALFORMATION, and it was missing from this dict while its
        # detector sat in the same module as `is_attempted_dsn`. The argument
        # above generalises verbatim: `backend` cannot carry it either, because
        # an unexpanded `${SCITEX_CARDS_DB}` is not DSN-shaped, so `backend_of`
        # cannot name the store and `exists` answers False -- both true of the string
        # and neither true of the intent, exactly as ":55432" once read as a
        # fresh install.
        #
        # `reject_unexpanded_variable` already guards the doors that OPEN a
        # store (_paths, _backend_connect, _db), and its own docstring says why
        # it does not raise here: "Resolution stays total and silent so a caller
        # that merely REPORTS a target can SHOW the ambiguity instead of raising
        # on it." This dict is that caller. The detector was built for this
        # surface and this surface did not consult it.
        #
        # Measured 2026-08-21 by claude-code-telegrammer: with the literal
        # `${SCITEX_CARDS_DB}` set, this verb named a file as the backend,
        # target_is_malformed_dsn=False and exit 0, while an actual read refused
        # (exit 1). The system was safe; the DIAGNOSTIC said nothing, which is
        # the surface an agent runs precisely when it is confused.
        "target_is_unexpanded_variable": is_unexpanded_variable(target),
        # THREE-VALUED, and None is not a hedge. "Does this file exist" has no
        # answer for a server, and BOTH poles actively mislead: False reads as
        # "your store is missing" to every operator staring at a cutover, True
        # would assert a reachability this function is forbidden to test (it is
        # pure reporting — it never opens anything). Read `backend` to know
        # which question was asked.
        "exists": None if on_server else Path(resolved).exists(),
        "store_uuid": observed_uuid,
        "expected_uuid": pinned_uuid,
        # Probed ONCE and compared in-process. `check_resolution` would re-run
        # the whole resolution and open a second connection to say the same
        # thing, and a diagnostic that costs two round-trips to a store that may
        # be down is a diagnostic that hangs twice as long on the case it exists
        # to explain.
        #
        # BOTH HALVES ARE HANDED TO THE COMPARISON, and until 2026-08-19 they
        # were not: the two uuid values were computed for the REPORT on the
        # lines above and never passed into `_check_against`, which compared the
        # instance alone. So this verb printed `expected_uuid` and `store_uuid`
        # differing on adjacent lines and answered `"identity_verdict":
        # "matches"` beneath them. The values being in scope is what made the
        # omission invisible.
        **_identity_fields(
            _check_against(
                instance_at(resolved),
                pinned_instance(),
                observed_uuid=observed_uuid,
                expected_uuid=pinned_uuid,
            )
        ),
    }


def _identity_fields(check) -> dict:
    """Flatten an :class:`._store_instance.IdentityCheck` into report keys.

    FLAT, not nested, because this dict is rendered by the CLI, the MCP tool and
    the board alike, and a nested object is the shape every one of them would
    have to learn separately. ``instance_id`` sits beside ``store_uuid`` for the
    same reason the uuid pair sits together: the facts you compare belong on one
    surface.

    ``identity_reason`` is carried verbatim rather than summarised. It is the
    only part a human acts on, and the two refusals — "you are pointed at the
    wrong store" and "I cannot tell which store this is" — call for different
    actions, which a boolean cannot say.
    """
    return {
        "instance_id": check.observed.instance_id,
        "expected_instance": check.expected,
        "identity_verdict": check.verdict.value,
        "identity_reason": check.reason,
        "may_proceed": check.may_proceed,
    }


def get_task(
    store: str | Path | None = None,
    task_id: str | None = None,
) -> dict:
    """Return a single task by id, or raise ``TaskNotFoundError``.

    Companion to ``add_task`` / ``update_task`` / ``list_tasks`` — the
    natural "read one" verb every CRUD surface expects but the Python
    API was missing (PR #56 audit gap). The MCP wrapper exposes this as
    ``get_task`` per Convention A.

    A TOMBSTONED row (see :func:`scitex_cards._task._is_tombstoned`) is
    treated as NOT FOUND — the 2026-07-21 tombstone change keeps a
    deleted card's row on disk forever, but this read must behave exactly
    as it did when ``delete_task`` physically removed it.

    READS ONE ROW. This verb used to load the whole board under the store lock
    and scan it for the id — a full export per call, which the notification
    dispatcher pays on EVERY card event (measured 2026-09-02: 1.2 s of a 2.7 s
    comment). It now reads the one row through the canonical read's own guards
    (:mod:`scitex_cards._store_single_card`), takes no lock (a one-row read has
    nothing to serialise against), and returns the card exactly as the export
    would have rebuilt it. ``store`` still names the caller's logical store for
    messages and sidecars; the row is read from the resolved store target, as
    the whole-document read always did.
    """
    from . import _task
    from ._store_single_card import read_card_or_raise
    from ._store_target import resolve_store_target

    if not task_id:
        raise ValueError("get_task: 'task_id' is required")
    card, _revision = read_card_or_raise(resolve_store_target(None), task_id)
    if card is None or _task._is_tombstoned(card):
        raise _task_not_found(task_id)
    return card


# --------------------------------------------------------------------------- #
# RE-EXPORTS — the moved verbs (PURE MOVE; this stays their import site)       #
# --------------------------------------------------------------------------- #
# Imported at the BOTTOM, after the shared helpers above are defined: the moved
# modules pull `_read_write_doc` / `_utc_now_iso` / `_default_agent` /
# `TaskNotFoundError` back OUT of this module (deferred, inside their function
# bodies), so the helpers must exist by the time any of them RUNS. A split that
# does not re-export is a rename with extra steps — every caller and test uses
# `from ._store import <verb>` / `_store.<verb>`, and those must keep resolving.
from ._store_comment import comment_task  # noqa: E402,F401  (re-export)
from ._store_events import (  # noqa: E402,F401  (re-export)
    _emit_card_event,
    _emit_unblock_for_dependents,
)
from ._store_lifecycle import (  # noqa: E402,F401  (re-export)
    complete_task,
    delete_task,
    reassign_task,
    reopen_task,
    resolve_task,
    restore_task,
)
from ._store_mutate import (  # noqa: E402,F401  (re-export)
    _stamp_deferred_at,
    _wip_statuses,
    add_task,
    update_task,
)
from ._store_reassign import reassign_all  # noqa: E402,F401  (re-export)
from ._store_relations import (  # noqa: E402,F401  (re-export)
    _set_list_member,
    set_collaborator,
    set_edge,
    set_subscriber,
)
from ._store_rescore import rescore_task  # noqa: E402,F401  (re-export)

__all__ = [
    "ENV_AGENT",
    "ENV_SCOPE",
    "StoreShrinkRefusedError",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reassign_all",
    "reassign_task",
    "reopen_task",
    "rescore_task",
    "resolve_store",
    "resolve_task",
    "restore_task",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
    "summarize_tasks",
    "update_task",
]

# EOF
