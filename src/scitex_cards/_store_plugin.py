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
a wrong one loses data WITHOUT RAISING — so every field states its rule and its
reason, and the reason is the deliverable.

THE REASON IS THE PART THAT GETS CHECKED. Two rules were wrong in the first
draft for the same reason: a field was described by what it OUGHT to be rather
than by what the code does to it. `last_activity` "only ever moves forward" was
a plausible sentence about a timestamp and a false statement about this column
(`_store_mutate.py:403` — see its entry in `_store_plugin_promotions`). When a
rule's reason names a line of code it can be checked; when it names an
intuition it cannot.

═══ WHAT IS DECLARED: THE DOCUMENT, NOT THE COLUMNS (ADR-0018 D1) ═══

`TASK_FIELDS` has TWO entries and only one of them carries card data: the
record key, and the card document. ADR-0018 D1 settles the question an earlier
draft of this file left open — "either it is derived (recompute after merge) or
it is canonical (and the columns are derived) … a schema decision this package
has not made yet" — in the second direction. The document is CANONICAL; the ~30
typed columns beside it are the INDEX (`_db_payload.py:23`), rebuilt from the
card on every write, and so are not separately replicated data.

The hazard that decides it is the draft's own, unchanged by the resolution: a
denormalised copy merged per-field BESIDE the document it duplicates lets the
two representations disagree — host A's `status` column beside host B's
`card_json.status` — producing a row that is internally inconsistent while
every field merged "correctly".

Declaring the columns INSTEAD of the document is the other direction, and it
loses data rather than merely confusing it: 22 distinct card keys have no
column at all (`_db_payload.py:9-18`, measured 2026-07-13 on 1,452 cards) —
including `parked`, whose absence on the receiving host makes the backlog sweep
propose CANCELLATION for cards nobody abandoned (ADR-0018:58-64).

THE COST, stated here rather than buried. Two agents editing DIFFERENT scalar
fields of the SAME card concurrently WILL clobber each other: the later HLC
wins the whole document, so an edit to `note` on host A and an edit to
`priority` on host B do not merge, and nothing raises. That is accepted
deliberately, as a comparison of failure modes — the losing value is still in
the append-only oplog (`_merge.py:11-18`: "'losing' a merge is not data loss —
it is a view"), whereas the 22 dropped keys would be gone on every host,
forever, silently. Silence is the failure mode this store cannot afford; three
board wipes are why ADR-0016 exists.

THE ~30 COLUMN RULES SURVIVE, IN A REGISTER RATHER THAN IN THE SCHEMA.
ADR-0018's escape hatch is to promote a genuinely contended field to its own
column "with a stated reason, one at a time" (:162-167), and
`_store_plugin_promotions.PROMOTION_CANDIDATES` is where those stated reasons
live until each promotion happens. It is re-exported here for callers, but it
is NOT a declaration: `task_schema()` reads `TASK_FIELDS` alone.

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
from ._store_plugin_promotions import PROMOTION_CANDIDATES, Promotion

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

#: The column holding the verbatim card — THE one declared payload. Named once
#: here rather than spelled at each use, because ADR-0018 N1 recommends
#: renaming it to `canonical_card`: `card_json` says how the value is ENCODED
#: and says nothing about the property a reader must not get wrong, that this
#: is the truth and the columns beside it are derived from it. The operator had
#: to ask what `card_json` was, which is the bug report. That rename is a
#: MIGRATION over a live store rather than a rename (ADR-0018:265-297), so it
#: has not happened; routing the declaration through one constant is what makes
#: it cheap when it does.
DOCUMENT_COL = "card_json"


def _p(kind, role, required, merge, indexed=False) -> FieldPolicy:
    """Every attribute keyword-only and stated — the primitive has no defaults."""
    return FieldPolicy(
        kind=kind, role=role, required=required, merge=merge, indexed=indexed
    )


# --------------------------------------------------------------------------- #
# The declaration. Two entries: the record key, and the card itself.           #
# --------------------------------------------------------------------------- #
TASK_FIELDS: "dict[str, FieldPolicy]" = {
    # IDENTITY — a card id is the card. It MUST also be IMMUTABLE, and the
    # primitive enforces that rather than trusting me: my first draft wrote
    # LAST_WRITER_WINS here and FieldPolicy refused it at construction with
    # "changing one does not update the record, it names a different record".
    # That is the validator earning its place — the one field where a wrong
    # rule would have silently merged two different cards into one.
    #
    # IT IS THE ONE TYPED COLUMN THAT STAYS DECLARED UNDER D1, and not by
    # preference. `Schema.build` REFUSES a schema with no IDENTITY field —
    # "without one there is no record key, so single-writer-per-record
    # ownership and oplog replay have nothing to attach to" (`_policy.py`) — so
    # this entry is structural: it is the record KEY, not replicated payload.
    # It is also the one duplicated column that CANNOT produce the internal
    # inconsistency D1 exists to prevent: the column and `card_json["id"]` are
    # both immutable, so they have no way to diverge. Being declared already,
    # it is not a promotion candidate — there is nothing to promote it to.
    "id": _p(FieldKind.TEXT, FieldRole.IDENTITY, True, MergeRule.IMMUTABLE),
    # THE CARD ITSELF, WHOLE, UNDER LAST_WRITER_WINS. This is ADR-0018 D1.
    #
    # kind=JSON describes the VALUE, not the storage class: the column is
    # `card_json TEXT` in SQLite (`_db_schema_sql.py`) and what it holds is a
    # document. FieldKind is dialect-independent by construction.
    #
    # LAST_WRITER_WINS rather than a collection rule: the card is a mapping one
    # agent edits as a unit, not a set two hosts extend independently. The
    # genuinely multi-writer parts are ADR-0018 D2 and live in their own tables
    # — `comments[]` under APPEND, edges and roles under UNION. `comments[]`
    # additionally CANNOT be declared yet: its elements carry no minted id, and
    # the autoincrement key the schema does have is "worse than no id at all"
    # (`_merge.py:214-219`) because two hosts both mint id=8 and replay drops
    # one comment while every count still looks right (ADR-0018 Q2).
    #
    # TWO LIFECYCLE FACTS MUST LEAVE THIS DOCUMENT BEFORE IT GOVERNS LIVE DATA
    # (ADR-0018 D3), and they are among the reasons the entry point stays
    # unregistered. `_log_meta.deleted_at` is the SOLE delete marker
    # (`_task.py:183-186`), so under LWW a later `defer` on another host brings
    # its whole document, the tombstone is simply not in it, and the card is
    # LIVE AGAIN on every host — a resurrection with no error and no conflict,
    # because from the merge's point of view the rule worked. The fix is
    # upstream's `FieldRole.HIDE_FLAG` as a real BOOL column; `completed_at`,
    # "the SOLE input to the throughput/timeline aggregates" which never
    # consult `status` (`_store_lifecycle.py:37-42`), promotes beside it.
    DOCUMENT_COL: _p(FieldKind.JSON, FieldRole.DATA, False, MergeRule.LAST_WRITER_WINS),
}

#: Fields DELIBERATELY NOT DECLARED AND NOT PROMOTABLE EITHER, each for a
#: stated reason. Absence here is a decision, not an oversight — see
#: `undeclared_fields_and_why()`. What separates this dict from
#: `PROMOTION_CANDIDATES` is that promotion is not the answer for these: no
#: per-field rule describes them, so giving them a column would not help.
UNDECLARED: "dict[str, str]" = {
    "row_order": (
        "DERIVED, NOT A VALUE. Board order is a projection over the whole "
        "table, not a fact about one card. Every MergeRule member is per-field "
        "by construction, so any choice yields duplicate and missing positions "
        "in what is supposed to be a total order. Needs a DERIVED / "
        "NOT_REPLICATED role upstream; until then it must be recomputed "
        "locally after merge, never replicated. Raised with scitex-dev."
    ),
    "revision": (
        "OWNED BY THE PRIMITIVE. scitex_dev.store reserves `_revision`; cards' "
        "own `revision` column is the pre-federation hand-rolled equivalent and "
        "must not be merged as data. It is dropped when the primitive takes "
        "over, not declared alongside it. CONTESTED, and flagged rather than "
        "quietly settled: ADR-0016:215-217 says the opposite in as many words "
        "— that preserving `revision` across a store copy is necessary because "
        "'it is user-visible causal state and belongs in the checksummed "
        "column set, not treated as backend bookkeeping'. Both cannot be true. "
        "ADR-0018 Q1 records the disagreement without resolving it."
    ),
}


def task_schema() -> Schema:
    """The cards table as a declared schema — built, so errors surface here.

    Reads `TASK_FIELDS` and nothing else. `PROMOTION_CANDIDATES` is a register
    of reasoning, not a second declaration, and must not reach the schema by
    accident: a candidate that merged would be exactly the denormalised
    per-field copy beside the document that ADR-0018 D1 exists to keep out.
    """
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
            # would promise an invariant the board breaks hourly, and the
            # document rule above exists precisely because it does.
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
    "DOCUMENT_COL",
    "PACKAGE",
    "PROMOTION_CANDIDATES",
    "STORE_NAME",
    "TASK_FIELDS",
    "UNDECLARED",
    "Promotion",
    "provide",
    "task_schema",
    "undeclared_fields_and_why",
]

# EOF
