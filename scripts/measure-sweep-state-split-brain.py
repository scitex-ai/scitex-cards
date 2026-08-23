#!/usr/bin/env python3
"""Is `sweep_state` dual-managed, or is SQLite its only home? READ-ONLY.

scitex-dev measured a live SQLite file on compute-04 whose data tables are all
EMPTY — the migration worked — except `sweep_state`, 140 rows, whose newest
`updated_at` matched the file mtime to the second. So something is still
writing it. They correctly declined to say which case it is, and named the
discriminator:

    dual-managed  -> two homes that DISAGREE. An inconsistency.
    only home     -> a migration MISS. A different fix.

This decides it by asking both stores the same question.

WHY IT MATTERS BEYOND TIDINESS. `sweep_state` holds the reminder/nudge cadence
— count, last_at, fingerprint, per owner. Constitution §3 puts runtime state in
the per-host Postgres on 55432, synchronized across hosts, never SQLite. If the
cadence lives in a per-host SQLite file then every host has its OWN idea of who
was nudged and when, and the delivery floor that suppresses duplicates is
computed from whichever copy the caller happened to open.

AND IT HAS REGROWN ONCE ALREADY. A sibling directory is literally named
`.old/20260818T0245Z-regrown-sqlite/`, so this was cleaned up and came back
inside two days. Deleting the file again without finding the writer would be the
third instance, not a fix.

READ-ONLY on both sides: Postgres sets default_transaction_read_only and proves
the guard armed; SQLite is opened with `mode=ro` in the URI so the connection
cannot write — deliberately, because opening a SQLite db read-write creates
side files and this is a live one.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter

import psycopg
import scitex as stx

PG = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
SQLITE = os.environ.get("CARDS_SQLITE", "/home/ywatanabe/.scitex/cards/cards.db")


def _prove_read_only(conn) -> None:
    try:
        conn.execute("CREATE TABLE _sweep_probe_should_fail (x int)")
    except psycopg.errors.ReadOnlySqlTransaction:
        return
    raise SystemExit("FATAL: read-only guard NOT armed on Postgres")


@stx.session
def main() -> int:
    print(f"postgres : {PG}")
    print(f"sqlite   : {SQLITE}\n")

    with psycopg.connect(PG + "?connect_timeout=10", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        _prove_read_only(conn)
        pg_rows = conn.execute(
            "SELECT scope, section, entry_key, payload_json, updated_at "
            "FROM public.sweep_state"
        ).fetchall()

    # mode=ro: a read-write open of a live SQLite db creates -wal/-shm side
    # files, so the instrument would perturb the thing it measures.
    uri = f"file:{SQLITE}?mode=ro"
    with sqlite3.connect(uri, uri=True) as lite:
        lite_rows = lite.execute(
            "SELECT scope, section, entry_key, payload_json, updated_at "
            "FROM sweep_state"
        ).fetchall()

    print("═══ ROW COUNTS ═══")
    print(f"  postgres sweep_state : {len(pg_rows)}")
    print(f"  sqlite   sweep_state : {len(lite_rows)}")
    print()

    pg_newest = max((str(r[4]) for r in pg_rows), default="(none)")
    lite_newest = max((str(r[4]) for r in lite_rows), default="(none)")
    print("═══ NEWEST WRITE — is the SQLite one still LIVE? ═══")
    print(f"  postgres newest updated_at : {pg_newest}")
    print(f"  sqlite   newest updated_at : {lite_newest}")
    print()

    def key(r):
        return (str(r[0]), str(r[1]), str(r[2]))

    pg_keys = {key(r) for r in pg_rows}
    lite_keys = {key(r) for r in lite_rows}
    both = pg_keys & lite_keys

    print("═══ THE DISCRIMINATOR scitex-dev NAMED ═══")
    print(f"  keys in BOTH stores        : {len(both)}")
    print(f"  keys ONLY in postgres      : {len(pg_keys - lite_keys)}")
    print(f"  keys ONLY in sqlite        : {len(lite_keys - pg_keys)}")
    if both:
        print("  -> DUAL-MANAGED: the same cadence keys exist in two places.")
    elif lite_keys:
        print("  -> SQLITE-ONLY for these keys: a migration MISS, not a conflict.")
    print()

    # Where they overlap, do they AGREE? A shared key with different payloads is
    # two hosts' worth of cadence disagreeing about who was nudged and when.
    if both:
        pg_by = {key(r): r[3] for r in pg_rows}
        lite_by = {key(r): r[3] for r in lite_rows}
        differing = [k for k in both if pg_by[k] != lite_by[k]]
        print("═══ WHERE BOTH HOLD A KEY, DO THEY AGREE? ═══")
        print(f"  shared keys with DIFFERENT payloads : {len(differing)} / {len(both)}")
        for k in sorted(differing)[:6]:
            print(f"    {k}")
            for label, blob in (("pg   ", pg_by[k]), ("lite ", lite_by[k])):
                try:
                    d = json.loads(blob) if isinstance(blob, str) else blob
                    shown = {x: d.get(x) for x in ("count", "last_at", "delivered_at")}
                except Exception:
                    shown = str(blob)[:80]
                print(f"      {label}{shown}")
        print()

    print("═══ WHAT SECTIONS ARE IN THE SQLITE COPY ═══")
    for (scope, section), n in Counter(
        (str(r[0]), str(r[1])) for r in lite_rows
    ).most_common():
        print(f"    {scope}/{section:<24} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
