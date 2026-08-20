#!/usr/bin/env python3
"""WHO cancelled 1,609 cards, and by WHAT criterion? Measured, not inferred.

scitex-dev measured the SHAPE (45-50 cancellations per minute across several
minutes on 2026-08-19) and correctly declined to name the criterion. This
answers the two questions they could not: the ACTOR and the KEY.

WHY I AM NOT ASSUMING IT WAS MY OWN AUTO-EXPIRY. `_backlog_triage` has an
expiry horizon (DEFAULT_EXPIRY_DAYS = 7.0, `deferred` only, `parked` exempt),
but `_cli/_triage.py` only PRINTS the expired set — and card
`cards-auto-expiry-is-a-report-nothing-schedules-and-three-horizons-disagree-20260817`
records that nothing schedules it. So the obvious suspect may not be the actor,
and "my module has a rule that matches" is a hypothesis, not a finding.

The fingerprint to look for: `cancelled_by_rule` appears on 111 cards in the
merge census and appears NOWHERE in this package's source. Something outside
scitex-cards writes it.

READ-ONLY. Sets default_transaction_read_only and proves the guard armed.
"""

from __future__ import annotations

import json
from collections import Counter

import psycopg
import scitex as stx

HUB = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
BURST_DAY = "2026-08-19"


def _prove_read_only(conn) -> None:
    try:
        conn.execute("CREATE TABLE _bulk_probe_should_fail (x int)")
    except psycopg.errors.ReadOnlySqlTransaction:
        return
    raise SystemExit("FATAL: read-only guard NOT armed")


@stx.session
def main() -> int:
    with psycopg.connect(HUB + "?connect_timeout=10", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        _prove_read_only(conn)
        rows = conn.execute("SELECT id, card_json FROM public.tasks").fetchall()

    cards = {}
    for task_id, doc in rows:
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                doc = {}
        cards[task_id] = doc if isinstance(doc, dict) else {}

    cancelled = {k: v for k, v in cards.items() if v.get("status") == "cancelled"}
    print(f"total cards {len(cards)} | cancelled {len(cancelled)}\n")

    # ── 1. THE FINGERPRINT FIELD ────────────────────────────────────────────
    with_rule = {k: v for k, v in cancelled.items() if v.get("cancelled_by_rule")}
    print("=== `cancelled_by_rule` — the field no source in this package writes ===")
    print(f"  cancelled cards carrying it : {len(with_rule)} / {len(cancelled)}")
    vals = Counter(str(v.get("cancelled_by_rule")) for v in with_rule.values())
    for val, n in vals.most_common(10):
        print(f"    {val:<48} {n}")
    print()

    # ── 2. THE BURST: what do the cards cancelled that minute have in common? ─
    burst = {k: v for k, v in cancelled.items()
             if str(v.get("last_activity", "")).startswith(BURST_DAY)}
    print(f"=== cards cancelled on {BURST_DAY} (by last_activity) : {len(burst)} ===")

    by_minute: Counter[str] = Counter()
    for v in burst.values():
        by_minute[str(v.get("last_activity", ""))[:16]] += 1
    print("  top minutes:")
    for minute, n in by_minute.most_common(8):
        print(f"    {minute}  {n}")
    print()

    # ── 3. DID THE ACTOR LEAVE A TRACE? ─────────────────────────────────────
    print("=== does a cancelled card carry ANY reason? ===")
    no_comment = sum(1 for v in burst.values() if not v.get("comments"))
    has_rule = sum(1 for v in burst.values() if v.get("cancelled_by_rule"))
    print(f"  burst cards with NO comments at all      : {no_comment} / {len(burst)}")
    print(f"  burst cards carrying cancelled_by_rule   : {has_rule} / {len(burst)}")

    # Last comment author on burst cards — if a sweep commented, it shows here.
    last_authors: Counter[str] = Counter()
    for v in burst.values():
        cs = v.get("comments") or []
        last_authors[str(cs[-1].get("author")) if cs else "(no comments)"] += 1
    print("  last-comment author on burst cards:")
    for a, n in last_authors.most_common(8):
        print(f"    {a:<28} {n}")
    print()

    # ── 4. THE KEY: what was true of the burst cards BEFORE cancellation? ────
    print("=== was the criterion PRIORITY-blind? ===")
    prio = Counter(str(v.get("priority")) for v in burst.values())
    for p, n in sorted(prio.items()):
        print(f"    priority {p:<8} {n}")
    print()

    print("=== were they all `deferred` before? (deferred_at present) ===")
    with_def = sum(1 for v in burst.values() if v.get("deferred_at"))
    print(f"  burst cards carrying deferred_at : {with_def} / {len(burst)}")
    print()

    # ── 5. ONE SWEEP OR TWO? The tag covers 111 of 955, so the rest were
    #       cancelled by something that left no attribution. If the tagged and
    #       untagged sets occupy DIFFERENT minutes they are different
    #       operations; if they interleave, one sweep tagged only some.
    print("=== TAGGED vs UNTAGGED — same operation or two? ===")
    tagged_min: Counter[str] = Counter()
    untagged_min: Counter[str] = Counter()
    for v in burst.values():
        m = str(v.get("last_activity", ""))[:16]
        (tagged_min if v.get("cancelled_by_rule") else untagged_min)[m] += 1
    allmins = sorted(set(tagged_min) | set(untagged_min))
    print(f"  minutes touched by TAGGED   : {len(tagged_min)}")
    print(f"  minutes touched by UNTAGGED : {len(untagged_min)}")
    print(f"  minutes where BOTH appear   : {len(set(tagged_min) & set(untagged_min))}")
    print("  minute                tagged  untagged")
    for m in allmins[:24]:
        print(f"    {m}   {tagged_min.get(m,0):>5}   {untagged_min.get(m,0):>7}")
    print()

    # ── 6. WHAT DID THE UNTAGGED ONES LOOK LIKE? ────────────────────────────
    untagged = {k: v for k, v in burst.items() if not v.get("cancelled_by_rule")}
    print(f"=== the {len(untagged)} UNTAGGED cancellations ===")
    print("  status before is unrecoverable, but these ARE recoverable:")
    st = Counter(str(v.get("blocker")) for v in untagged.values())
    for k, n in st.most_common(6):
        print(f"    blocker={k:<24} {n}")
    p1 = [k for k, v in untagged.items() if v.get("priority") in (0, 1)]
    print(f"  priority 0/1 among untagged : {len(p1)}")
    for k in p1[:12]:
        print(f"    {k}")
    print()

    print("=== were any PARKED (which should have exempted them)? ===")
    parked = [k for k, v in burst.items()
              if isinstance(v.get("parked"), str) and v["parked"].strip()]
    print(f"  burst cards with a non-empty park reason : {len(parked)}")
    for k in parked[:10]:
        print(f"    {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
