#!/usr/bin/env python3
"""READ-ONLY extract of THIS host's card store to a portable dump.

WHY THIS EXISTS. `fleet-semantic-merge.py` dials the compute nodes on their
overlay addresses. ywata-note-win has NO overlay address — measured, `ip -4
addr` shows no 100.64.x — and its Postgres binds 127.0.0.1 behind a password in
that host's own PGPASSFILE. So it cannot be dialled, and the earlier divergence
census SILENTLY OMITTED it: a four-host report that read as a fleet report,
missing precisely the replica holding the stale statuses.

Running in place is the honest fix. The alternative is carrying that host's
credential to the hub, which trades a reachability problem for a secret-handling
one. This ships back data, never a credential.

NOT NAMED FOR ywata-note-win ON PURPOSE. Nothing in here is specific to that
host — it reads whatever store the host it runs on has, and labels the dump with
its own hostname. A host name in the filename would be a claim about scope that
the code does not make.

USAGE, from anywhere:
    scp scripts/extract-cards-on-this-host.py <host>:/tmp/
    ssh <host> <a-venv-python-with-scitex-and-psycopg> /tmp/extract-cards-on-this-host.py
    scp <host>:/tmp/cards_dump.json.gz .
    YNW_DUMP=./cards_dump.json.gz <venv>/python scripts/fleet-semantic-merge.py

On ywata-note-win the venv with both deps is
/home/ywatanabe/proj/scitex-cards/.venv/bin/python (.env-3.11 has psycopg but an
older scitex).
"""

from __future__ import annotations

import gzip
import json
import os
import socket
import sys

import psycopg
import scitex as stx

DSN = os.environ.get(
    "CARDS_DSN", "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards?connect_timeout=10"
)
OUT = os.environ.get("CARDS_DUMP_OUT", "/tmp/cards_dump.json.gz")


@stx.session
def main() -> int:
    os.environ.setdefault("PGPASSFILE", os.path.expanduser("~/.pgpass"))

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")

        # PROVE the guard is armed rather than assuming it. An unarmed guard and
        # an armed one are indistinguishable right up until something writes,
        # and this runs against a live 5,600-card store.
        try:
            conn.execute("CREATE TABLE _extract_probe_should_fail (x int)")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
        else:
            print("FATAL: read-only guard NOT armed — refusing to read", file=sys.stderr)
            return 2

        def q(sql: str):
            return conn.execute(sql).fetchone()

        identity = {
            "host": socket.gethostname(),
            "database": q("SELECT current_database()")[0],
            # The one value a copy cannot carry: minted by initdb, in no dump.
            "system_identifier": str(q("SELECT system_identifier FROM pg_control_system()")[0]),
            # A schema_meta ROW, so pg_dump copies it — it identifies LINEAGE,
            # which is why three hosts share one and it cannot identify a store.
            "store_uuid": (q("SELECT value FROM public.schema_meta WHERE key='store_uuid'") or [None])[0],
            "schema_version": (q("SELECT value FROM public.schema_meta WHERE key='schema_version'") or [None])[0],
            "rows": q("SELECT count(*) FROM public.tasks")[0],
        }
        rows = conn.execute("SELECT id, card_json FROM public.tasks").fetchall()

    cards: dict[str, dict] = {}
    for task_id, doc in rows:
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                doc = {"__unparseable__": True}
        cards[task_id] = doc if isinstance(doc, dict) else {"__not_a_dict__": True}

    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump({"identity": identity, "cards": cards}, fh)

    print(json.dumps(identity, indent=2))
    print(f"wrote {OUT} ({len(cards)} cards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
