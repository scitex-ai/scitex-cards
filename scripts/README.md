# scripts/

Maintenance and operational shell scripts. **Not** part of the installed
package — nothing here is importable, and none of it is on `$PATH` after a
`pip install`. These are run deliberately, by hand, by someone who came here
looking for them.

That is the point of the directory: it is where work that must persist but
must not be a daily surface goes to live. A one-shot migration does not earn
a CLI verb; it does earn a home that survives a reboot.

| script | what it is for |
|---|---|
| `install-cards-git-hooks.sh` | Install this repo's git hooks into a checkout. |
| `cutover-freeze-writers.sh` | Freeze / thaw the fleet's card-store writers for the multi-host cutover. |
| `fleet-divergence-census.py` | READ-ONLY: measure field-level divergence between the fleet's card stores. |

## `fleet-divergence-census.py`

Run it with `/home/ywatanabe/.env-sac/bin/python` — the only venv on compute-04
carrying both `scitex` (for the session decorator) and `psycopg`.

Every connection sets `default_transaction_read_only`, so a mistake here cannot
mutate a store. It diffs `card_json` — the document ADR-0018 D1 declares as the
truth — rather than the ~29 typed columns that duplicate it, because diffing
those would measure the copy.

It reports, per peer: rows unique to each side, rows differing, status forks,
and the subset where one side is terminal and the other still active. That last
number is the one that matters — it is the class `MergeRule.MAX` gets wrong,
since on TEXT `MAX` is lexicographic and picks `in_progress` over `done`.

Its status-fork counts were cross-checked against the running peer sync's own
independently-computed figures and matched exactly, which is what makes it a
measurement rather than an opinion.

## `cutover-freeze-writers.sh`

`--dry-run` (default posture) / `--freeze` / `--thaw`.

Three things about it are load-bearing and easy to undo by accident:

1. **`scitex-cards-pg.service` is deny-listed and must stay that way.** It is
   the PostgreSQL server holding the board. Its name shares the
   `scitex-cards-` prefix with every writer, so `systemctl --user stop
   'scitex-cards-*'` would take the database down with them and leave nothing
   to `pg_dump`. Units are named explicitly for this reason, and the deny-list
   is re-checked at the point of action rather than only when the plan is built.

2. **`--freeze` requires `CUTOVER_I_MEAN_IT=yes`.** Added after it fired by
   accident during a rehearsal and stopped two live services. The gate is
   checked *before* the survey, so testing the gate is itself safe. `--dry-run`
   stays free — the safe path must remain the cheap one, or people route around
   it.

3. **`--thaw` restarts only what `--freeze` recorded stopping**, and refuses
   without that record. Restarting "everything in the plan" would start units
   that were already down for their own reasons, which is a change wearing a
   reversal's clothes.

The freeze record is written locally *and* mirrored to the tracking card
(`cards-quiesce-state-refuses-writes-during-cutover-20260731`) in PostgreSQL,
because it answers "what must be restarted" — an answer that has to read the
same for whoever performs the restore. A record that dies with the shell that
wrote it is not a record; that is not hypothetical, it happened once already.

Hosts that cannot be reached are **listed in the plan anyway** and reported as
`UNREACHABLE`. Omitting them would print a clean four-host run that reads as
full fleet coverage, so the one host nobody can stop would be the one host
nobody sees.
