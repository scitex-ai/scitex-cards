#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PROMOTION REGISTER — the ~30 rules that are reasoned but not declared.

ADR-0018 D1 declares the card DOCUMENT and leaves the typed columns beside it
derived, so none of the columns in this file is replicated data. That decision
does not make the reasoning about how each one WOULD merge wrong, and most of
that reasoning was expensive to get right — one entry here was wrong in the
first draft because the field was described by what it OUGHT to be rather than
by what the code does to it (`last_activity`, below).

WHY THE REASONING IS KEPT RATHER THAN DELETED. ADR-0018 accepts a real,
named cost — two agents editing DIFFERENT scalar fields of the SAME card
concurrently clobber each other, because the later HLC wins the whole document
— and it names exactly one remedy, with a limit:

    When a specific field turns out to be genuinely contended, promote *that
    field* to its own column, with a stated reason, one at a time — exactly as
    D3 does for the tombstone and `completed_at`. **Never invert the whole read
    path.**
    — ADR-0018:162-167

This module is where those stated reasons live until each promotion happens, so
the argument for a promotion starts from recorded evidence instead of from a
fresh guess by whoever needs it that week.

AN ENTRY HERE IS NOT A DECLARATION. Nothing in `PROMOTION_CANDIDATES` reaches
the schema — `_store_plugin.task_schema()` reads `TASK_FIELDS` alone, and a
test pins that the typed columns the document duplicates are absent from it.
Each entry does carry a real, CONSTRUCTED `FieldPolicy`, so the primitive's own
validator runs over the intended rule today rather than on the day someone
promotes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from scitex_dev.store import FieldKind, FieldPolicy, FieldRole, MergeRule


@dataclass(frozen=True, kw_only=True, slots=True)
class Promotion:
    """A column that is NOT declared, the rule it would take, and why.

    `policy` is constructed rather than described, so a rule that the primitive
    would refuse fails at import instead of at promotion time. `blocked_on`
    names a prerequisite that must land elsewhere first — a promotion cannot be
    made correct by wanting it — and `None` is the positive claim that nothing
    stands in the way of this one.
    """

    policy: FieldPolicy
    why: str
    blocked_on: "str | None" = None


def _c(kind, required, merge, why, *, indexed=False, blocked_on=None) -> Promotion:
    """One candidate. `role` is DATA for every one of them, by construction.

    IDENTITY is not a candidate: the record key is already declared in
    `TASK_FIELDS` and cannot be promoted twice. HIDE_FLAG is not one either,
    though the REASON changed in v13: the column now EXISTS (`is_deleted`,
    added by `_migrate_v12_to_v13` per ADR-0018 D3), but this helper hard-codes
    `role=FieldRole.DATA`, and a hide flag declared as DATA would be a
    different field with the same name. It is recorded in `UNDECLARED`
    instead, with the declaration it is waiting for.
    """
    return Promotion(
        policy=FieldPolicy(
            kind=kind, role=FieldRole.DATA, required=required, merge=merge,
            indexed=indexed,
        ),
        why=why,
        blocked_on=blocked_on,
    )


#: The reason shared by the plain scalars. Stated once because it IS one
#: reason, not because repeating it was tedious: nothing about these fields is
#: monotone, write-once or a collection, so the most recent edit is the
#: intended one and the HLC makes "most recent" the same answer on every host.
_ORDINARY_SCALAR = (
    "ORDINARY SCALAR: the most recent edit is the intended one, and the HLC "
    "makes 'most recent' the same answer on every host. Not monotone, not "
    "write-once, not a collection — LAST_WRITER_WINS is the rule rather than "
    "the fallback."
)


def _lww(*, indexed: bool = False) -> Promotion:
    """A plain TEXT scalar under LWW — the shape most of this table has."""
    return _c(
        FieldKind.TEXT, False, MergeRule.LAST_WRITER_WINS, _ORDINARY_SCALAR,
        indexed=indexed,
    )


#: Columns NOT declared, each with the rule it would take and why.
PROMOTION_CANDIDATES: "dict[str, Promotion]" = {
    "created_at": _c(
        FieldKind.TEXT,
        False,
        MergeRule.IMMUTABLE,
        "IMMUTABLE — written once at creation. Two hosts cannot disagree "
        "about when a card was created unless one of them is wrong, and "
        "picking the later value would let a re-import rewrite history.",
    ),
    "created_by": _c(
        FieldKind.TEXT,
        False,
        MergeRule.IMMUTABLE,
        "IMMUTABLE, for the same reason as `created_at`: who created a card is "
        "a fact about one moment. A second, different value is a domain "
        "contradiction to be investigated, and the primitive reports it as a "
        "conflict rather than dropping it (`_merge.py:96-115`).",
    ),
    "last_activity": _c(
        FieldKind.TEXT,
        False,
        MergeRule.LAST_WRITER_WINS,
        "NOT MAX, and the reason is not taste. An earlier draft wrote MAX on "
        "the claim that this stamp 'only ever moves forward'. THAT CLAIM IS "
        "FALSE: `_store_mutate.py:403` auto-stamps only `if \"last_activity\" "
        "not in fields` — a caller who passes the field explicitly keeps their "
        "own value, and it IS public, on `add_task`/`update_task` "
        "(`_mcp_write.py:63,180`) and on the CLI (`_cli/_write.py:190`). Any "
        "agent can write any string. MAX IS UNREPAIRABLE FOR A FIELD WITH A "
        "PUBLIC SETTER: `merge_field` compares the VALUES, not the stamps "
        "(`_merge.py:121-138`: `wins = incoming > current`), so one writer "
        "setting '9999-01-01' replicates that value to every host and every "
        "real timestamp then loses to it FOREVER — including the repair, "
        "because a corrected timestamp is a LOWER value and MAX rejects it by "
        "construction. There is no in-band fix; you would be editing rows on "
        "every host by hand. LAST_WRITER_WINS is repairable: the repair is "
        "simply the newest write, and the HLC makes it win everywhere. It buys "
        "that at the cost of letting a stale-but-later write move the stamp "
        "backwards — a wrong recency colour on the board, which is visible and "
        "self-correcting on the next real edit. A recoverable wrong pixel "
        "beats a permanent unfixable one.",
    ),
    "finished_at": _c(
        FieldKind.TEXT,
        False,
        MergeRule.LAST_WRITER_WINS,
        "SAME SHAPE AS `last_activity`, same conclusion, stronger case. It is "
        "public on the same two surfaces (`_mcp_write.py:73,190`, "
        "`_cli/_write.py:218`) and, unlike `last_activity`, the package NEVER "
        "derives it — `_validate.py:344` classes it compute-only, so a "
        "caller-supplied string is its ONLY source. That makes it strictly "
        "less monotonic than `last_activity`, not more, so MAX is at least as "
        "unrepairable here. It is also legitimately re-written LOWER when a "
        "card is reopened and finished again, which MAX would silently refuse.",
    ),
    "status": _c(
        FieldKind.TEXT,
        True,
        MergeRule.LAST_WRITER_WINS,
        "THE LEAST-BAD AVAILABLE RULE, NOT A CORRECT ONE. This is the one "
        "candidate whose promotion is BLOCKED rather than merely unscheduled, "
        "and promoting it would make the board worse, not better. `status` is "
        "a LIFECYCLE with legal transitions, not a free scalar, and LWW "
        "RESURRECTS: a locally `done` or `cancelled` card meets a peer that "
        "still believes it active, the peer's write carries the later HLC, and "
        "the card is open again with no error. That is measured, not feared — "
        "against ONE peer store sac found 439 status forks, 303 of them a card "
        "locally TERMINAL (done/cancelled) that the peer believes active. "
        "MAX IS NOT A SUBSTITUTE, for a mechanical reason rather than an "
        "aesthetic one: `merge_field` compares the VALUES "
        "(`_merge.py:121-138`), and these values are strings, so the "
        "comparison is LEXICOGRAPHIC — 'in_progress' > 'done' and "
        "'in_progress' > 'cancelled'. MAX resurrects the same cards LWW does, "
        "and worse: it does so even when the terminal write is strictly newer, "
        "and it does so permanently, because the repair is a lower value.",
        indexed=True,
        blocked_on=(
            "An upstream LIFECYCLE-LATCH rule — a merge that knows a terminal "
            "state is terminal and refuses to leave it, instead of ordering "
            "two opaque strings. Every rule the primitive has today is either "
            "value-ordered (MAX), stamp-ordered (LAST_WRITER_WINS), "
            "write-once (IMMUTABLE) or a collection rule (APPEND / UNION), and "
            "none of them can express 'done does not go back to in_progress'. "
            "Promoting `status` to its own column before that rule exists "
            "would replicate the resurrection per-field instead of "
            "per-document: the same wrong answer, reached more confidently."
        ),
    ),
    "blocker": _lww(),
    "title": _c(FieldKind.TEXT, True, MergeRule.LAST_WRITER_WINS, _ORDINARY_SCALAR),
    "kind": _lww(),
    "task": _lww(),
    "note": _lww(),
    "goal": _lww(),
    "project": _lww(indexed=True),
    "repo": _lww(),
    "agent": _lww(indexed=True),
    "assignee": _lww(indexed=True),
    "scope": _lww(indexed=True),
    "grp": _lww(),
    "priority": _c(
        FieldKind.INTEGER, False, MergeRule.LAST_WRITER_WINS, _ORDINARY_SCALAR
    ),
    "parent": _lww(),
    "pr_url": _lww(),
    "issue_url": _lww(),
    "deadline": _lww(),
    "scheduled": _lww(),
    "job_id": _lww(),
    "command": _lww(),
    "deadlines_json": _c(
        FieldKind.JSON,
        False,
        MergeRule.LAST_WRITER_WINS,
        "A SIDE-CAR ENCODING of the card's `deadlines` list (`json_or_none`, "
        "`_db_payload.py`). It is a list but NOT a multi-writer collection: a "
        "card's deadlines are rewritten as a whole by one caller, never "
        "extended independently by two hosts, so UNION — the rule its shape "
        "suggests — would resurrect a deadline the last editor deliberately "
        "removed.",
    ),
    "log_meta_json": _c(
        FieldKind.JSON,
        False,
        MergeRule.LAST_WRITER_WINS,
        "THE LIFECYCLE STAMPS side-car (`_log_meta`: deleted_at, deleted_by, "
        "completed_at, completed_by). LWW is right for the RESIDUE and wrong "
        "for two of its members, which is why ADR-0018 D3 promotes those two "
        "OUT of the blob rather than promoting the blob: the delete tombstone "
        "becomes a BOOL `FieldRole.HIDE_FLAG` column — upstream built the role "
        "for exactly this, `_policy.py:68-70` — and `completed_at`, 'the SOLE "
        "input to the throughput/timeline aggregates' "
        "(`_store_lifecycle.py:37-42`), gets its own. ADR-0018 N2 also renames "
        "it: `meta` means 'data about data', which describes every column in "
        "the table, and what this holds is lifecycle stamps.",
        blocked_on=(
            "ADR-0018 D3 first: the tombstone and `completed_at` leave this "
            "blob for real columns. Promoting the blob whole would keep the "
            "sole delete marker inside a value that LWW replaces wholesale — "
            "the resurrection, one indirection further in — and promoting the "
            "residue before D3 lands would look like progress while changing "
            "nothing that matters."
        ),
    ),
    "host": _c(
        FieldKind.TEXT,
        False,
        MergeRule.LAST_WRITER_WINS,
        "`host` is the SUBJECT of the row, not its provenance — sac's "
        "distinction, 2026-08-12: `_origin` is 'which node told me'; a host "
        "column is 'which machine this row is ABOUT'. They coincide only while "
        "one writer owns each record and diverge the moment a row is relayed. "
        "Populating this from 'the node that wrote it' would be right until "
        "exactly the cross-host case it exists for, so it merges as ordinary "
        "data and is never derived from `_origin`.",
    ),
    "started_at": _c(
        FieldKind.TEXT,
        False,
        MergeRule.LAST_WRITER_WINS,
        "NOT MAX, deliberately. The truthful value is the EARLIEST observation "
        "(work began when it began), and the primitive has no MIN rule. LWW is "
        "the honest placeholder; MAX would be actively wrong, quietly moving a "
        "start time later every time a second host reports one.",
        blocked_on=(
            "A MIN rule upstream. Promoting this column under LWW would "
            "promote the placeholder rather than the intended semantics, and "
            "the placeholder is only tolerable while the column is derived."
        ),
    ),
    # THE TWO v13 STAMPS. Added as columns by _migrate_v12_to_v13 so the
    # lifecycle facts can be merged INDEPENDENTLY of `card_json`, which
    # last-writer-wins replaces whole. Candidates rather than declarations
    # because a sibling test pins TASK_FIELDS at exactly two and growing it
    # is an ADR-0018 D1 decision in its own right.
    "completed_at": _c(
        FieldKind.TEXT,
        False,
        MergeRule.MAX,
        "MAX, and it is sound here for the reason it is NOT sound for "
        "`status`. _db_sync_columns measured that trap: MAX compares VALUES, "
        "so on TEXT it is lexicographic and a locally `cancelled` card loses "
        "to a stale peer's `in_progress` (439 status forks against one peer, "
        "303 of them locally terminal). A TIMESTAMP escapes it because "
        "lexicographic order over fixed-width ISO-8601 UTC IS chronological "
        "order. That makes completion monotone: no stale peer's document can "
        "un-record that the work finished. THE CONSTRAINT TRAVELS WITH THE "
        "RULE — every writer must emit one fixed-width UTC format; a mixed "
        "corpus (+09:00 offsets, variable precision, a bare date) breaks MAX "
        "silently and in exactly the same way.",
        indexed=True,
    ),
    "reopened_at": _c(
        FieldKind.TEXT,
        False,
        MergeRule.MAX,
        "THE PAIR TO `completed_at`, and the reason neither needs a rule the "
        "primitive lacks. A monotone stamp cannot be lowered, so completion "
        "alone would make a card unreopenable — upstream raises exactly this "
        "objection when it forbids MAX on a hide flag ('MergeRule.MAX in "
        "particular would make a hide permanent'). Two monotone stamps avoid "
        "the deadlock instead of trading it: each only moves forward, and the "
        "presented state derives from whichever is later, so reopening is an "
        "ordinary write rather than an attempt to undo one. Same fixed-width "
        "UTC constraint as its pair.",
        indexed=True,
    ),
}

__all__ = ["PROMOTION_CANDIDATES", "Promotion"]

# EOF
