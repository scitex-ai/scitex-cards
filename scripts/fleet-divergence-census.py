#!/usr/bin/env python3
"""READ-ONLY field-level divergence census across the fleet's card stores.

The input to the reconciliation, and deliberately NOT the reconciliation: it
measures what differs and writes nothing anywhere. Every connection sets
`default_transaction_read_only`, so a coding mistake here cannot mutate a store.

WHY FIELD-LEVEL AND NOT ROW-LEVEL. compute-01 is a strict SUBSET of the hub --
0 unique rows, purely N behind -- so a row-level union expresses nothing about
the difference. The peer sync is ADDITIVE-ONLY: rows propagate, field updates do
not. That produces "same rows, different values", which only a per-field census
can describe.

WHY card_json AND NOT THE TYPED COLUMNS. ADR-0018 D1: `TASK_FIELDS` declares
exactly the card document (JSON) and `id`. The ~29 typed columns duplicate the
document and are a promotion register, not the truth. Diffing them would measure
the copy.

RUN IT WITH `/home/ywatanabe/.env-sac/bin/python` — that is the only venv on
this host carrying BOTH `scitex` (for the session decorator) and `psycopg`. The
project venv `proj/scitex-cards/.venv` has psycopg but no scitex, and the agent
container has neither scitex nor host VPN routing worth relying on.
"""

from __future__ import annotations

import json
from collections import Counter

import psycopg
import scitex as stx

HUB = ("compute-04", "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
PEERS = [
    ("compute-01", "postgresql://scitex_cards@100.64.0.2:55432/scitex_cards"),
    ("compute-02", "postgresql://scitex_cards@100.64.0.3:55432/scitex_cards"),
    ("compute-03", "postgresql://scitex_cards@100.64.0.4:55432/scitex_cards"),
]

# Terminal states, MEASURED terminal on this board (149 in / 1 out). A fork
# where one side is terminal and the other is not is the dangerous class: it is
# what makes the digest announce done cards as actionable, and it is what MAX
# gets wrong, because on TEXT MAX is lexicographic.
TERMINAL = {"done", "cancelled", "failed", "completed"}


def load(dsn: str) -> dict[str, dict]:
    with psycopg.connect(dsn + "?connect_timeout=10", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        rows = conn.execute("SELECT id, card_json FROM public.tasks").fetchall()
    out = {}
    for task_id, doc in rows:
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                doc = {"__unparseable__": True}
        out[task_id] = doc if isinstance(doc, dict) else {"__not_a_dict__": True}
    return out


@stx.session
def main() -> None:
    hub_name, hub_dsn = HUB
    hub = load(hub_dsn)
    print(f"hub {hub_name}: {len(hub)} cards\n")

    for peer_name, peer_dsn in PEERS:
        peer = load(peer_dsn)
        only_hub = hub.keys() - peer.keys()
        only_peer = peer.keys() - hub.keys()
        both = hub.keys() & peer.keys()

        field_forks: Counter[str] = Counter()
        differing_rows = 0
        status_forks = 0
        terminal_vs_active = 0

        for task_id in both:
            h, p = hub[task_id], peer[task_id]
            diff = [k for k in (h.keys() | p.keys()) if h.get(k) != p.get(k)]
            if not diff:
                continue
            differing_rows += 1
            field_forks.update(diff)
            if "status" in diff:
                status_forks += 1
                hs, ps = h.get("status"), p.get("status")
                # The asymmetric case: one side has decided, the other has not.
                if (hs in TERMINAL) != (ps in TERMINAL):
                    terminal_vs_active += 1

        print(f"=== {hub_name} vs {peer_name} ===")
        print(f"  rows only on hub      {len(only_hub)}")
        print(f"  rows only on peer     {len(only_peer)}")
        print(f"  rows in both          {len(both)}")
        print(f"  of those, DIFFERING   {differing_rows}")
        print(f"  status forks          {status_forks}")
        print(f"    terminal vs active  {terminal_vs_active}  <- MAX gets these wrong")
        print("  top forked fields:")
        for field, count in field_forks.most_common(12):
            print(f"    {field:<24} {count}")
        print()


if __name__ == "__main__":
    main()
