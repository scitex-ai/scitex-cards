#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards' merge semantics, declared for scitex-dev's store primitive.

THE OPERATOR'S INSTRUCTION (Telegram, 2026-08-10):「scitex-dev と協働して
scitex-cards がマルチホストで使えるように、scitex-dev が提供する db primitive を
使ってください、自前で書いている部分を scitex-dev のものを利用して下さい」— use
scitex-dev's primitive, drop the hand-rolled parts.

WHAT THIS MODULE OWNS, AND WHY IT CANNOT LIVE UPSTREAM. scitex-dev supplies the
machinery (HLC, oplog, replay, `merge_field`); it cannot know that a card's
`created_at` is written once, that `last_activity` only ever moves forward, or
that `status` is a lifecycle rather than a free scalar. Only this package knows
that. There is NO DEFAULT merge rule, and a wrong one loses data WITHOUT
RAISING — so every field below states its rule and its reason, and the reason
is the deliverable.

═══ WHY `provide()` DEGRADES INSTEAD OF RAISING ═══

`StorePlugin` requires scitex-dev >= 0.49. On an older install `provide()`
returns [] rather than raising, because `discover_store_plugins` CATCHES a
raising provider and continues — so a raise would be indistinguishable from a
leaf that declares nothing, i.e. a dead plugin that reports success.

A CORRECTION WORTH KEEPING, because it nearly cost this work a day. I first
concluded the federation did not exist at all, from two checks that were both
about ARTIFACTS rather than the fact:

    `_federation.py` absent from origin/main and origin/develop   TRUE, and
                                                                  IRRELEVANT
    StorePlugin absent from my installed scitex_dev 0.48.0        TRUE, and
                                                                  STALE

The released 0.49.3 wheel contains NO file named `_federation.py` — the
internals were reorganised — and EXPORTS `StorePlugin` regardless. I had tested
for a filename and for a three-release-old local install, and reported "the
federation is not available" to another agent on that basis. Test for the
symbol you need, in the environment that will run it.
"""

from __future__ import annotations

from typing import Any

from scitex_dev.store import FieldKind, FieldPolicy, FieldRole, MergeRule, Schema

#: The table this package owns. `name == schema.name` is the federation's dedup
#: key, so it must stay stable once declared.
STORE_NAME = "scitex_cards_tasks"

#: The owning distribution, reported to the federation for provenance.
PACKAGE = "scitex-cards"


def _p(kind, role, required, merge, indexed=False) -> FieldPolicy:
    """Every attribute keyword-only and stated — the primitive has no defaults."""
    return FieldPolicy(
        kind=kind, role=role, required=required, merge=merge, indexed=indexed
    )


# --------------------------------------------------------------------------- #
# The card. Every field states a rule and the reason it is that rule.          #
# --------------------------------------------------------------------------- #
TASK_FIELDS: "dict[str, FieldPolicy]" = {
    # IDENTITY — a card id is the card. It MUST also be IMMUTABLE, and the
    # primitive enforces that rather than trusting me: my first draft wrote
    # LAST_WRITER_WINS here and FieldPolicy refused it at construction with
    # "changing one does not update the record, it names a different record".
    # That is the validator earning its place — the one field where a wrong
    # rule would have silently merged two different cards into one.
    "id": _p(FieldKind.TEXT, FieldRole.IDENTITY, True, MergeRule.IMMUTABLE),
    # IMMUTABLE — written once at creation. Two hosts cannot disagree about
    # when a card was created unless one of them is wrong, and picking the
    # later value would let a re-import rewrite history.
    "created_at": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.IMMUTABLE),
    "created_by": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.IMMUTABLE),
    # MAX — monotone stamps. `last_activity` only ever moves forward; taking
    # the later of two observations is both correct and idempotent.
    "last_activity": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.MAX),
    "finished_at": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.MAX),
    # LAST_WRITER_WINS, FLAGGED: `status` is a LIFECYCLE with legal
    # transitions, not a free scalar. LWW can resurrect a `cancelled` card into
    # `blocked` — sac measured exactly that split on the live board. It is the
    # least-bad rule AVAILABLE, not a correct one, and it should be revisited
    # if the primitive ever grows a transition-aware rule.
    "status": _p(FieldKind.TEXT, FieldRole.DATA, True, MergeRule.LAST_WRITER_WINS, True),
    "blocker": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    # Ordinary scalars: the most recent edit is the intended one.
    "title": _p(FieldKind.TEXT, FieldRole.DATA, True, MergeRule.LAST_WRITER_WINS),
    "kind": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "task": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "note": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "goal": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "project": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS, True),
    "repo": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "agent": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS, True),
    "assignee": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS, True),
    "scope": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS, True),
    "grp": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "priority": _p(FieldKind.INTEGER, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "parent": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "pr_url": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "issue_url": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "deadline": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "scheduled": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "job_id": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "command": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "deadlines_json": _p(FieldKind.JSON, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    "log_meta_json": _p(FieldKind.JSON, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    # `host` is the SUBJECT of the row, not its provenance — sac's distinction,
    # 2026-08-12: `_origin` is "which node told me"; a host column is "which
    # machine this row is ABOUT". They coincide only while one writer owns each
    # record and diverge the moment a row is relayed. Populating this from "the
    # node that wrote it" would be right until exactly the cross-host case it
    # exists for, so it merges as ordinary data and is never derived from
    # `_origin`.
    "host": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
    # `started_at` is NOT MAX, deliberately. The truthful value is the EARLIEST
    # observation (work began when it began), and the primitive has no MIN
    # rule. LWW is the honest placeholder; MAX would be actively wrong, quietly
    # moving a start time later every time a second host reports one.
    "started_at": _p(FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
}

#: Fields DELIBERATELY NOT DECLARED, each for a stated reason. Absence here is a
#: decision, not an oversight — see `undeclared_fields_and_why()`.
UNDECLARED: "dict[str, str]" = {
    "row_order": (
        "DERIVED, NOT A VALUE. Board order is a projection over the whole "
        "table, not a fact about one card. Every MergeRule member is per-field "
        "by construction, so any choice yields duplicate and missing positions "
        "in what is supposed to be a total order. Needs a DERIVED / "
        "NOT_REPLICATED role upstream; until then it must be recomputed "
        "locally after merge, never replicated. Raised with scitex-dev."
    ),
    "card_json": (
        "A DENORMALISED COPY OF EVERY OTHER COLUMN. Merging it per-field "
        "alongside the columns it duplicates lets the two representations "
        "disagree — host A's `status` column beside host B's `card_json.status` "
        "— producing a row that is internally inconsistent while every field "
        "merged 'correctly'. Either it is derived (recompute after merge) or it "
        "is canonical (and the columns are derived). That is a schema decision "
        "this package has not made yet, and guessing it silently corrupts rows."
    ),
    "revision": (
        "OWNED BY THE PRIMITIVE. scitex_dev.store reserves `_revision`; cards' "
        "own `revision` column is the pre-federation hand-rolled equivalent and "
        "must not be merged as data. It is dropped when the primitive takes "
        "over, not declared alongside it."
    ),
}


def task_schema() -> Schema:
    """The cards table as a declared schema — built, so errors surface here."""
    return Schema.build(STORE_NAME, TASK_FIELDS)


def undeclared_fields_and_why() -> "dict[str, str]":
    """Fields intentionally absent from the schema, with the reason for each.

    Exposed as a function rather than left in a comment so a test can assert
    the set, and so a reviewer can see that absence was decided rather than
    forgotten.
    """
    return dict(UNDECLARED)


def provide() -> "list[Any]":
    """Entry-point provider — returns [] until the federation exists.

    NOT YET WIRED: `scitex_dev.store.StorePlugin` is absent from installed
    0.48.0 and from origin/main and origin/develop (measured 2026-08-14). This
    returns an EMPTY LIST rather than raising, because a raising provider is
    swallowed by `discover_store_plugins` and would be indistinguishable from a
    leaf that declares nothing.

    It is also not registered in pyproject yet, so nothing calls this in
    production — the emptiness cannot be mistaken for a live declaration.
    """
    try:
        from scitex_dev.store import StorePlugin, WriterPolicy
    except ImportError:
        return []
    return [
        StorePlugin(
            name=STORE_NAME,
            pkg=PACKAGE,
            schema=task_schema(),
            # MULTI_WRITER, and this is a semantic choice rather than a
            # default. SINGLE_WRITER means exactly one writer may append ops
            # for a record, so divergence cannot arise — which is a stronger
            # guarantee cards CANNOT make: any agent may comment on, reassign
            # or complete any card, from any host. Declaring SINGLE_WRITER
            # would promise an invariant the board breaks hourly, and every
            # merge rule above exists precisely because it does.
            writer_policy=WriterPolicy.MULTI_WRITER,
            provider=f"{__name__}:provide",
            description="scitex-cards task board — cards and their lifecycle.",
        )
    ]


__all__ = [
    "PACKAGE",
    "STORE_NAME",
    "TASK_FIELDS",
    "provide",
    "task_schema",
    "undeclared_fields_and_why",
]

# EOF
