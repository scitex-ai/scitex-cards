#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards' merge semantics, declared for scitex-dev's store primitive.

THE OPERATOR'S INSTRUCTION (Telegram, 2026-08-10):「scitex-dev と協働して
scitex-cards がマルチホストで使えるように、scitex-dev が提供する db primitive を
使ってください、自前で書いている部分を scitex-dev のものを利用して下さい」— use
scitex-dev's primitive, drop the hand-rolled parts.

WHAT THIS MODULE OWNS, AND WHY IT CANNOT LIVE UPSTREAM. scitex-dev supplies the
machinery (HLC, oplog, replay, `merge_field`); it cannot know that a card's
`created_at` is written once, that `last_activity` is stamped automatically but
can also be SET by any caller, or that `status` is a lifecycle rather than a
free scalar. Only this package knows that. There is NO DEFAULT merge rule, and
a wrong one loses data WITHOUT RAISING — so every field below states its rule
and its reason, and the reason is the deliverable.

THE REASON IS THE PART THAT GETS CHECKED. Two rules here were wrong in the
first draft for the same reason: a field was described by what it OUGHT to be
rather than by what the code does to it. `last_activity` "only ever moves
forward" was a plausible sentence about a timestamp and a false statement about
this column (`_store_mutate.py:379` — see its entry). When a rule's reason
names a line of code, it can be checked; when it names an intuition, it cannot.

═══ WHY `provide()` RAISES INSTEAD OF DEGRADING ═══

`StorePlugin` requires scitex-dev >= 0.49. On an older install `provide()`
RAISES the ImportError. An earlier draft returned [] instead and argued that a
raise would be indistinguishable from a leaf that declares nothing — which is
exactly backwards, and the upstream source says so:
`scitex_dev/store/federation/_discover.py:113-122` catches a raising provider
and emits `_logger.warning("Skipping store plugins from provider %r: it
raised.", ..., exc_info=True)`, and :96-99 states the intent — one broken leaf
"must not stop every other leaf's store from resolving — but it must not pass
unnoticed either, hence the warning with a traceback."

So RAISING is the LOUD branch: a warning naming this provider, with a
traceback. RETURNING [] is the SILENT one — indistinguishable from a leaf that
genuinely declares nothing, and therefore the dead plugin that reports success.

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

from ._paths import PKG_SHORT

#: The table this package owns. `name == schema.name` is the federation's dedup
#: key, so it must stay stable once declared.
STORE_NAME = "scitex_cards_tasks"

#: The declaring pip DISTRIBUTION — `StorePlugin.provider`, which upstream
#: documents as "The declaring pip package (``\"scitex-cards\"``). Carried so a
#: federation listing can say who is responsible for a declaration"
#: (`scitex_dev/store/federation/_spec.py:60-63`). It is PROVENANCE and nothing
#: else.
#:
#: It is NOT `StorePlugin.pkg`, and an earlier draft passed it as one. `pkg` is
#: the package SHORT name and it "Decides where the store resolves ... two
#: plugins naming different ``pkg`` values resolve to different stores"
#: (:46-52). Passing "scitex-cards" there resolved a DIFFERENT, empty store from
#: the live board, silently — no validator can catch it, because both strings
#: are non-empty and plausible.
#:
#: Cards' short name is `PKG_SHORT` (`_paths.py:32`, value "cards"), imported
#: above rather than re-spelled here: a second spelling of the same fact is how
#: this bug happens, and re-typing the literal would reintroduce the drift the
#: import exists to prevent.
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
    # NOT MAX, and the reason is not taste. An earlier draft wrote MAX here on
    # the claim that `last_activity` "only ever moves forward". THAT CLAIM IS
    # FALSE: `_store_mutate.py:379` auto-stamps only `if "last_activity" not in
    # fields` — a caller who passes the field explicitly keeps their own value,
    # and it IS public, on `add_task`/`update_task` (`_mcp_write.py:63,180`) and
    # on the CLI (`_cli/_write.py:190`). Any agent can write any string.
    #
    # MAX IS UNREPAIRABLE FOR A FIELD WITH A PUBLIC SETTER. `merge_field`
    # compares the VALUES, not the stamps (`_merge.py:121-138`: `wins = incoming
    # > current`), so one writer setting "9999-01-01" replicates that value to
    # every host and then every real timestamp loses to it FOREVER — including
    # the repair, because a corrected timestamp is a LOWER value and MAX
    # rejects it by construction. There is no in-band fix; you would be editing
    # rows on every host by hand.
    #
    # LAST_WRITER_WINS is repairable: the repair is simply the newest write, and
    # the HLC makes it win everywhere. It buys that at the cost of letting a
    # stale-but-later write move the stamp backwards — a wrong recency colour on
    # the board, which is visible and self-correcting on the next real edit. A
    # recoverable wrong pixel beats a permanent unfixable one.
    "last_activity": _p(
        FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS
    ),
    # `finished_at` HAS THE SAME SHAPE, and the conclusion is the same. It is
    # public on the same two surfaces (`_mcp_write.py:73,190`,
    # `_cli/_write.py:218`) and, unlike `last_activity`, the package NEVER
    # derives it — `_validate.py:344` classes it compute-only, so a
    # caller-supplied string is its ONLY source. That makes it strictly less
    # monotonic than `last_activity`, not more, so MAX is at least as
    # unrepairable here. It is also legitimately re-written lower when a card is
    # reopened and finished again, which MAX would silently refuse.
    "finished_at": _p(
        FieldKind.TEXT, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS
    ),
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
    """Entry-point provider — the declaration, or a raise. Never a silent [].

    NOT REGISTERED. `pyproject.toml` deliberately omits the
    `scitex_dev.store.plugins` group (the omission is documented there, with
    what has to be decided before it goes in), so nothing calls this in
    production and this declaration governs no live data yet. That is checked,
    not remembered: `test_the_entry_point_is_not_wired_yet` reads the real
    pyproject.

    THE IMPORT IS NOT GUARDED, deliberately. `StorePlugin` needs scitex-dev >=
    0.49; on an older install this raises ImportError and
    `discover_store_plugins` logs `"Skipping store plugins from provider %r: it
    raised."` WITH a traceback (`federation/_discover.py:113-122`), which is
    upstream's stated intent at :96-99 — a broken leaf "must not stop every
    other leaf's store from resolving — but it must not pass unnoticed either".
    Returning [] would suppress that warning and make a dead plugin look like a
    healthy leaf that simply declares nothing. Raising is the LOUD branch.
    """
    from scitex_dev.store import StorePlugin, WriterPolicy

    return [
        StorePlugin(
            name=STORE_NAME,
            # `pkg` RESOLVES THE STORE (`federation/_spec.py:46-52`) — see
            # PACKAGE's note. Short name, from the single source in `_paths`.
            pkg=PKG_SHORT,
            schema=task_schema(),
            # MULTI_WRITER, and this is a semantic choice rather than a
            # default. SINGLE_WRITER means exactly one writer may append ops
            # for a record, so divergence cannot arise — which is a stronger
            # guarantee cards CANNOT make: any agent may comment on, reassign
            # or complete any card, from any host. Declaring SINGLE_WRITER
            # would promise an invariant the board breaks hourly, and every
            # merge rule above exists precisely because it does.
            writer_policy=WriterPolicy.MULTI_WRITER,
            # PROVENANCE — the declaring pip distribution, which is what a
            # federation listing prints and what a duplicate-name collision
            # names. Not the module path: `_spec.py:60-63` asks for the
            # package.
            provider=PACKAGE,
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
