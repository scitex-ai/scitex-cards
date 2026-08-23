#!/usr/bin/env python3
"""Does "hub wins" on note/title DESTROY anything? Measured, not assumed.

WHY THIS EXISTS. I asked the operator to rule that note/title conflicts resolve
hub-side, and attached the risk "peer-only additions would be lost" marked
UNMEASURED. That hands a human my uncertainty to carry. This measures it, so the
answer is evidence either way — and if the direction is not uniform, the question
as I posed it is wrong and must be withdrawn before he acts on it.

WHAT THE CLAIM RESTED ON. Ten examples the dry-run happened to print, ordered by
`Counter.most_common`. That is not a sample; it is whatever sorted first. The
claim may still be true — this decides it over all 5,637 cards rather than ten.

THE PREDICATE, and it is deliberately line-level rather than similarity-based.
A note is an append-heavy document: corrections are added ABOVE older text, and
the failure mode that matters is a peer holding a LINE the hub lacks. So:

    every non-trivial line of every peer value appears in the hub value
        -> SAFE. "Hub wins" loses nothing; the hub is a superset.
    some peer line is absent from the hub value
        -> AT RISK. That line is destroyed, and it is named in the output.

Similarity scoring would smooth exactly the case we care about — one added
paragraph in a 4KB note is a high similarity score and a total loss.

BLANK AND TRIVIAL LINES ARE EXCLUDED from the risk set: a peer having a blank
line the hub lacks is whitespace drift, not content, and counting it would bury
the real losses under noise. Short lines are kept — `═══ NEXT ═══` is content.

READ-ONLY. It reads the dumps and the live stores; the store connections set
default_transaction_read_only and prove the guard armed.

RUN WITH /home/ywatanabe/.env-sac/bin/python, and YNW_DUMP pointing at the
ywata-note-win dump (see scripts/extract-cards-on-this-host.py).
"""

from __future__ import annotations

import gzip
import json
import os
from collections import Counter

import psycopg
import scitex as stx

HUB = ("compute-04", "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
PEERS = [
    ("compute-01", "postgresql://scitex_cards@100.64.0.2:55432/scitex_cards"),
    ("compute-02", "postgresql://scitex_cards@100.64.0.3:55432/scitex_cards"),
    ("compute-03", "postgresql://scitex_cards@100.64.0.4:55432/scitex_cards"),
]
TEXT_FIELDS = ("note", "title")
DEFAULT_YNW_DUMP = "/tmp/ynw_cards_dump.json.gz"

# Markers that mean "this text is the CORRECTED side". Used only to DESCRIBE the
# population, never to decide a merge — "prefer the value containing a retraction
# marker" would be a rule that rewards writing the word, which is not a rule.
RETRACTION_MARKERS = (
    "RETRACTED", "WITHDRAWN", "CORRECTION", "SUPERSEDED", "was wrong",
    "premise false", "premise was false", "I was wrong", "撤回", "訂正",
)


def _prove_read_only(conn, label: str) -> None:
    try:
        conn.execute("CREATE TABLE _direction_probe_should_fail (x int)")
    except psycopg.errors.ReadOnlySqlTransaction:
        return
    raise SystemExit(f"FATAL: read-only guard NOT armed on {label}")


def _normalise(rows) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for task_id, doc in rows:
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                doc = {}
        out[task_id] = doc if isinstance(doc, dict) else {}
    return out


def load_pg(dsn: str, label: str) -> dict[str, dict]:
    with psycopg.connect(dsn + "?connect_timeout=10", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        _prove_read_only(conn, label)
        rows = conn.execute("SELECT id, card_json FROM public.tasks").fetchall()
    return _normalise(rows)


def load_dump(path: str) -> tuple[str, dict[str, dict]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        blob = json.load(fh)
    return blob["identity"]["host"], {
        k: (v if isinstance(v, dict) else {}) for k, v in blob["cards"].items()
    }


def content_lines(text) -> set[str]:
    """The lines that would be LOST if this value were discarded.

    Blank and rule-only lines are excluded: a peer holding whitespace the hub
    lacks is drift, and counting it would bury real losses in noise.
    """
    if not isinstance(text, str):
        return set()
    out = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if set(line) <= set("═─-=_*# "):  # a horizontal rule carries no content
            continue
        out.add(line)
    return out


def has_marker(text) -> bool:
    return isinstance(text, str) and any(m in text for m in RETRACTION_MARKERS)


@stx.session
def main() -> int:
    dump_path = os.environ.get("YNW_DUMP", DEFAULT_YNW_DUMP)
    if not os.path.exists(dump_path):
        raise SystemExit(f"REFUSING: no ywata-note-win dump at {dump_path}")

    hub_name, hub_dsn = HUB
    hub = load_pg(hub_dsn, hub_name)
    peers: list[tuple[str, dict[str, dict]]] = [(n, load_pg(d, n)) for n, d in PEERS]
    peers.append(load_dump(dump_path))

    print(f"hub {hub_name}: {len(hub)} cards")
    for n, c in peers:
        print(f"  peer {n:<16} {len(c)} cards")
    print()

    for field in TEXT_FIELDS:
        differing = 0
        hub_superset = 0
        at_risk: list[tuple[str, str, list[str]]] = []
        hub_missing_entirely = 0
        marker_side: Counter[str] = Counter()

        for task_id, hub_doc in hub.items():
            hub_val = hub_doc.get(field)
            hub_lines = content_lines(hub_val)

            card_at_risk: dict[str, list[str]] = {}
            card_differs = False

            for peer_name, peer_cards in peers:
                if task_id not in peer_cards:
                    continue
                peer_val = peer_cards[task_id].get(field)
                if peer_val == hub_val:
                    continue
                card_differs = True

                lost = sorted(content_lines(peer_val) - hub_lines)
                if lost:
                    card_at_risk[peer_name] = lost

            if not card_differs:
                continue
            differing += 1

            if hub_val in (None, ""):
                hub_missing_entirely += 1

            if card_at_risk:
                worst = max(card_at_risk.items(), key=lambda kv: len(kv[1]))
                at_risk.append((task_id, worst[0], worst[1]))
            else:
                hub_superset += 1

            # Describe which side carries the correction language.
            peer_marker = any(
                has_marker(pc[task_id].get(field))
                for _, pc in peers if task_id in pc
            )
            if has_marker(hub_val) and not peer_marker:
                marker_side["hub only"] += 1
            elif peer_marker and not has_marker(hub_val):
                marker_side["PEER only"] += 1
            elif peer_marker:
                marker_side["both"] += 1
            else:
                marker_side["neither"] += 1

        print(f"═══ {field.upper()} ═══")
        print(f"  cards where any store differs from hub : {differing}")
        print(f"  hub is a SUPERSET (hub-wins loses none) : {hub_superset}")
        print(f"  AT RISK (a peer line the hub lacks)     : {len(at_risk)}")
        print(f"  hub value empty/absent while a peer has one : {hub_missing_entirely}")
        print("  retraction language on:")
        for k, v in marker_side.most_common():
            print(f"      {k:<12} {v}")
        print()

        # THE RAW AT-RISK COUNT IS NOT A LOSS COUNT, and saying so is the
        # point of this block. `note` is REPLACED rather than appended on this
        # board, so when a note is rewritten on the hub the peer keeps the
        # PREVIOUS version and every dropped line reads as "at risk" while
        # actually being text its own author deliberately superseded. The
        # predicate cannot separate those. These two subsets CAN be decided
        # without reading intent:
        if field == "note":
            print("  --- A. HUB EMPTY, PEER HAS CONTENT: hub-wins BLANKS the card ---")
            blanked = 0
            for task_id, hub_doc in hub.items():
                if hub_doc.get(field) not in (None, ""):
                    continue
                for peer_name, peer_cards in peers:
                    pv = peer_cards.get(task_id, {}).get(field)
                    if isinstance(pv, str) and pv.strip():
                        blanked += 1
                        head = pv.strip().splitlines()[0][:100]
                        print(f"    {task_id}  [{peer_name}, {len(pv)} chars]")
                        print(f"        | {head}")
                        break
            print(f"    TOTAL unambiguous blanking: {blanked}\n")

            print("  --- B. PEER HOLDS RETRACTION LANGUAGE THE HUB LACKS ---")
            print("      (the INVERSE of the direction I claimed to the operator)")
            inverted = 0
            for task_id, hub_doc in hub.items():
                hv = hub_doc.get(field) or ""
                if has_marker(hv):
                    continue
                for peer_name, peer_cards in peers:
                    pv = peer_cards.get(task_id, {}).get(field)
                    if isinstance(pv, str) and has_marker(pv):
                        inverted += 1
                        hit = next(m for m in RETRACTION_MARKERS if m in pv)
                        line = next((ln.strip() for ln in pv.splitlines() if hit in ln), "")
                        print(f"    {task_id}  [{peer_name}]")
                        print(f"        | {line[:120]}")
                        break
            print(f"    TOTAL inverted: {inverted}\n")

        if field == "title":
            # STATED SO NOBODY READS 136/136 AS A FINDING. `title` is ONE line,
            # so "the hub contains every peer line" can only hold when the
            # values are identical — and identical values never reach this
            # branch. The at-risk figure for a single-line field is therefore a
            # TAUTOLOGY of the predicate, not evidence about direction. What is
            # informative for titles is the retraction-language split above.
            print("  !! `title` is single-line: AT RISK == differing, by construction.")
            print("     Read the retraction-language split, not this count.\n")

        if at_risk:
            print(f"  --- every AT-RISK card for `{field}`, so this is a list not a number ---")
            for task_id, peer_name, lost in sorted(at_risk, key=lambda t: -len(t[2])):
                print(f"    {task_id}  [{peer_name}, {len(lost)} line(s) would be lost]")
                for line in lost[:3]:
                    shown = line if len(line) <= 110 else line[:107] + "..."
                    print(f"        | {shown}")
                if len(lost) > 3:
                    print(f"        | ... and {len(lost) - 3} more")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
