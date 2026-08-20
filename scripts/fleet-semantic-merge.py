#!/usr/bin/env python3
"""READ-ONLY dry-run of the field-level SEMANTIC merge across the fleet's stores.

The reconciliation's plan, not the reconciliation. It computes what the merged
board WOULD be and reports it; it writes to no store. Every connection sets
`default_transaction_read_only`, and the guard is PROVEN armed on each one
rather than assumed — an unarmed guard and an armed one look identical right up
until something writes.

WHY SEMANTIC AND NOT LAST-WRITE-WINS. There is NO cross-host clock on this
board, measured:

    updated_at / origin_node / row_uuid    100% NULL      unusable
    revision                               LOCAL counter  meaningless across hosts
    last_activity                          COMMENTING touches it, so it tracks
                                           chatter, not decisions

So "newest wins" cannot be computed, and any tool that appears to do it is
reading a field that does not mean what its name says. Every rule below is
therefore SEMANTIC — it appeals to what the value MEANS, never to when it was
written — and each is stated so a human can disagree with it in review.

WHY card_json AND NOT THE TYPED COLUMNS. ADR-0018 D1: `TASK_FIELDS` declares
the card document plus `id`; the ~29 typed columns are a promotion register
duplicating it. Diffing them measures the copy.

WHY ywata-note-win COMES FROM A FILE. Its Postgres binds 127.0.0.1 with no
overlay address (measured: `ip -4 addr` shows no 100.64.x), so the hub cannot
dial it — which is exactly why the earlier divergence census silently omitted
it, and it is the peer holding the stale statuses. `scripts/extract-cards-on-this-host.py`
runs ON that host under its own PGPASSFILE and writes the dump this reads.
Passing it as a file is the honest shape: the alternative is carrying that
host's credential to the hub.

RUN WITH /home/ywatanabe/.env-sac/bin/python — the only venv here with both
`scitex` and `psycopg`.
"""

from __future__ import annotations

import gzip
import json
import os
from collections import Counter, defaultdict

import psycopg
import scitex as stx

DEFAULT_YNW_DUMP = "/tmp/ynw_cards_dump.json.gz"

HUB = ("compute-04", "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
PEERS = [
    ("compute-01", "postgresql://scitex_cards@100.64.0.2:55432/scitex_cards"),
    ("compute-02", "postgresql://scitex_cards@100.64.0.3:55432/scitex_cards"),
    ("compute-03", "postgresql://scitex_cards@100.64.0.4:55432/scitex_cards"),
]

# MEASURED terminal on this board (149 transitions in, 1 out), not assumed from
# the vocabulary. A state that is terminal by name but routinely reopened would
# make the LATCH below destructive.
TERMINAL = frozenset({"done", "cancelled", "failed", "completed"})

# THE TIERS, TAKEN FROM THE GRANTED UPSTREAM DESIGN — not invented here.
#
# The mission card records what scitex-dev GRANTED for MergeRule.LATCH, and
# this must agree with it or the one-shot merge and the eventual StorePlugin
# will disagree about the same cards:
#
#   ("pending",)                                            MEASURED lowest
#   ("archived","goal","deferred","blocked","in_progress")  MEASURED NOT
#                                                           monotone — these
#                                                           move BOTH ways, so
#                                                           any order among them
#                                                           is IMPOSED
#   ("cancelled","failed","completed","done")               MEASURED terminal,
#                                                           149 in / 1 out
#
# MY FIRST VERSION DIVERGED AND IT WAS A LIVE BUG. I ranked `deferred` alone at
# the bottom (reasoning: it is the writer default, so it may never have been
# chosen) and left `pending` UNRANKED — which fell through to the middle tier
# and therefore OUTRANKED `deferred`. `pending` was ABOLISHED 2026-07-10 and
# cards still carry it: the wake-watcher journal on ywata-note-win logs
# "TOLERATED (read-side): status 'pending' was abolished" for real ids right
# now. So the merge would have promoted an abolished status over a valid one.
#
# The "deferred is the default" observation still stands as an argument, but it
# is an argument for changing the UPSTREAM tuple, not for diverging from it
# here. Recorded, not acted on unilaterally.
TIER_ABOLISHED, TIER_ACTIVE, TIER_TERMINAL = 1, 2, 3
STATUS_TIER = {
    # Abolished 2026-07-10. Lowest: it is not a decision, it is a leftover.
    "pending": TIER_ABOLISHED,
    # Not monotone — cards move both ways through these, so a difference here
    # is two real positions and the within-tier CONFLICT below is the answer.
    **{v: TIER_ACTIVE for v in
       ("archived", "goal", "deferred", "blocked", "in_progress")},
    # Concluded. MEASURED terminal on this board — a state terminal only by
    # name would make this latch destructive.
    **{v: TIER_TERMINAL for v in ("done", "cancelled", "failed", "completed")},
}

# UNION, because these are sets whose elements are independently meaningful:
# losing an edge or a subscriber loses a relationship nobody restated.
UNION_FIELDS = frozenset({"depends_on", "blocks", "collaborators", "subscribers", "deadlines"})

# Element-wise union keyed by the element's own id. Comments are append-only in
# practice, so two stores hold overlapping prefixes of one thread.
THREAD_FIELDS = frozenset({"comments"})

# MAX is genuinely correct here and ONLY here: these are ISO-8601 Z strings, for
# which lexicographic order IS chronological. Applying MAX to any other TEXT
# field would silently mean "alphabetically largest", which is why the rule is
# an explicit allow-list rather than a type check.
MAX_FIELDS = frozenset({"last_activity", "finished_at", "started_at"})


def _prove_read_only(conn: psycopg.Connection, label: str) -> None:
    """Fail loudly unless a write actually cannot happen on this connection."""
    try:
        conn.execute("CREATE TABLE _merge_probe_should_fail (x int)")
    except psycopg.errors.ReadOnlySqlTransaction:
        return
    raise SystemExit(f"FATAL: read-only guard NOT armed on {label} — refusing to continue")


def load_pg(dsn: str, label: str) -> dict[str, dict]:
    with psycopg.connect(dsn + "?connect_timeout=10", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        _prove_read_only(conn, label)
        rows = conn.execute("SELECT id, card_json FROM public.tasks").fetchall()
    return _normalise(rows)


def load_dump(path: str) -> tuple[str, dict[str, dict]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        blob = json.load(fh)
    ident = blob["identity"]
    return ident["host"], {k: (v if isinstance(v, dict) else {}) for k, v in blob["cards"].items()}


def _normalise(rows) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for task_id, doc in rows:
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                doc = {"__unparseable__": True}
        out[task_id] = doc if isinstance(doc, dict) else {"__not_a_dict__": True}
    return out


def _elements_by_id(seq) -> dict:
    """Index a thread's elements by their own id, keeping insertion order."""
    if not isinstance(seq, list):
        return {}
    indexed = {}
    for i, el in enumerate(seq):
        key = el.get("id") if isinstance(el, dict) else None
        indexed[key if key is not None else f"__pos{i}__"] = el
    return indexed


def merge_field(field: str, values: list) -> tuple[str, object]:
    """Return (verdict, merged_value).

    verdict is one of:
      IDENTICAL  every store agrees; nothing to do
      AUTO       a stated rule resolves it without human judgement
      CONFLICT   the rules do not decide; a human must. NOT silently picked.
    """
    present = [v for v in values if v is not None]
    if not present:
        return "IDENTICAL", None

    # json round-trip so dict/list values compare structurally
    uniq = {json.dumps(v, sort_keys=True, default=str) for v in present}
    if len(uniq) == 1 and len(present) == len(values):
        return "IDENTICAL", present[0]

    if field in THREAD_FIELDS:
        merged: dict = {}
        for v in present:
            merged.update(_elements_by_id(v))
        return "AUTO", list(merged.values())

    if field in UNION_FIELDS:
        seen, merged_list = set(), []
        for v in present:
            for el in v if isinstance(v, list) else [v]:
                key = json.dumps(el, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    merged_list.append(el)
        return "AUTO", merged_list

    if field in MAX_FIELDS:
        strs = [v for v in present if isinstance(v, str)]
        return ("AUTO", max(strs)) if strs else ("CONFLICT", present)

    if field == "status":
        # TIERED LATCH. The tier ranks how much EVIDENCE OF A DECISION a value
        # carries — never how recent it is, because no cross-host clock exists.
        #
        # The bottom tier is principled, not arbitrary: `deferred` is the
        # DEFAULT in every writer (_task.py:343, _mcp_write.py:48,
        # _store_add.py:42), so a card sitting at `deferred` may never have been
        # decided at all. Every other value had to be chosen by someone. So a
        # chosen value outranks an unchosen one, and that is the whole rule.
        #
        # WITHIN a tier, differing values are two real decisions that disagree
        # about what happened — never auto-picked.
        # AN UNRANKED VALUE IS A NAMED CONFLICT, NEVER A GUESSED TIER. The
        # status domain is deliberately OPEN (operator ruling 2026-07-10: a
        # card must always be writable, warning is enough), so the tuple will
        # ALWAYS lag the data. Assigning an unknown value a default tier is
        # how `pending` silently outranked `deferred` in my first version.
        unranked = sorted({str(v) for v in present if str(v) not in STATUS_TIER})
        if unranked:
            return "CONFLICT", {"unranked": unranked, "values": sorted({str(v) for v in present})}

        best = max(STATUS_TIER[str(v)] for v in present)
        winners = {str(v) for v in present if STATUS_TIER[str(v)] == best}
        if len(winners) == 1:
            return "AUTO", next(iter(winners))
        # Same tier, different values: two real positions on a non-monotone
        # axis. Never auto-picked.
        return "CONFLICT", sorted(winners)

    # Free text and scalars. One side empty is an absence, not a disagreement.
    non_empty = [v for v in present if v not in ("", [], {}, None)]
    uniq_non_empty = {json.dumps(v, sort_keys=True, default=str) for v in non_empty}
    if len(uniq_non_empty) == 1:
        return "AUTO", non_empty[0]

    return "CONFLICT", present


@stx.session
def main() -> int:
    # NO FLAGS, AND THE DUMP IS REQUIRED — BOTH ON PURPOSE.
    #
    # `@stx.session` owns argv, so argparse cannot coexist with it. That
    # constraint pushed this to the shape it should have had anyway: the
    # PREVIOUS census made ywata-note-win optional by simply not listing it,
    # and so produced a four-host report that read as a fleet report. An
    # optional peer is an invitation to a partial answer that looks complete.
    #
    # Here the dump is an input the script cannot start without. Overridable
    # by env for a different path; never omittable.
    dump_path = os.environ.get("YNW_DUMP", DEFAULT_YNW_DUMP)
    if not os.path.exists(dump_path):
        raise SystemExit(
            f"REFUSING: no ywata-note-win dump at {dump_path}.\n"
            "  It has no overlay address, so it cannot be dialled like the other\n"
            "  peers, and it is the replica holding the stale statuses — a report\n"
            "  without it is not a fleet report.\n"
            "  Produce one with:  scp scripts/extract-cards-on-this-host.py ywata-note-win:/tmp/ &&\n"
            "    ssh ywata-note-win /home/ywatanabe/proj/scitex-cards/.venv/bin/python \\\n"
            "      /tmp/extract-cards-on-this-host.py\n"
            "  then scp back /tmp/ynw_cards_dump.json.gz, or set YNW_DUMP."
        )

    show_conflicts = int(os.environ.get("SHOW_CONFLICTS", "10"))
    stores: list[tuple[str, dict[str, dict]]] = []
    hub_name, hub_dsn = HUB
    stores.append((hub_name, load_pg(hub_dsn, hub_name)))
    for name, dsn in PEERS:
        stores.append((name, load_pg(dsn, name)))

    stores.append(load_dump(dump_path))

    for name, cards in stores:
        print(f"  {name:<16} {len(cards):>6} cards")
    print()

    all_ids: set[str] = set()
    for _, cards in stores:
        all_ids |= cards.keys()

    # WHICH STORE HOLDS WHAT — the row-level picture, before any field merging.
    only_in: Counter[str] = Counter()
    for task_id in all_ids:
        holders = [n for n, c in stores if task_id in c]
        if len(holders) == 1:
            only_in[holders[0]] += 1
    print(f"total distinct cards across the fleet : {len(all_ids)}")
    print("cards held by EXACTLY ONE store (would be lost by a naive 'hub wins'):")
    for name, _ in stores:
        print(f"    {name:<16} {only_in.get(name, 0):>6}")
    print()

    verdicts: Counter[str] = Counter()
    field_conflicts: Counter[str] = Counter()
    field_auto: Counter[str] = Counter()
    examples: dict[str, list] = defaultdict(list)
    cards_with_conflict = 0
    cards_changed = 0

    for task_id in sorted(all_ids):
        docs = [c[task_id] for _, c in stores if task_id in c]
        fields: set[str] = set()
        for d in docs:
            fields |= d.keys()

        card_conflict = False
        card_changed = False
        for field in fields:
            values = [d.get(field) for d in docs]
            verdict, merged = merge_field(field, values)
            verdicts[verdict] += 1
            if verdict == "AUTO":
                field_auto[field] += 1
                card_changed = True
            elif verdict == "CONFLICT":
                field_conflicts[field] += 1
                card_conflict = True
                if len(examples[field]) < show_conflicts:
                    examples[field].append((task_id, merged))
        cards_with_conflict += card_conflict
        cards_changed += card_changed

    print("═══ FIELD VERDICTS ═══")
    for v in ("IDENTICAL", "AUTO", "CONFLICT"):
        print(f"  {v:<10} {verdicts[v]:>7}")
    print()
    print(f"cards a rule would change            : {cards_changed}")
    print(f"cards needing a HUMAN decision       : {cards_with_conflict}")
    print()

    print("═══ AUTO-RESOLVED, by field (a stated rule decided these) ═══")
    for field, n in field_auto.most_common(15):
        print(f"  {field:<24} {n:>6}")
    print()

    print("═══ CONFLICTS, by field (NOT auto-picked — this is the human queue) ═══")
    for field, n in field_conflicts.most_common(15):
        print(f"  {field:<24} {n:>6}")
    print()

    for field, n in field_conflicts.most_common(5):
        print(f"--- example conflicts: {field} ({n} total) ---")
        for task_id, vals in examples[field]:
            rendered = json.dumps(vals, default=str)
            if len(rendered) > 160:
                rendered = rendered[:157] + "..."
            print(f"    {task_id}\n      {rendered}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
