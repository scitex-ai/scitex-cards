#!/usr/bin/env bash
# Freeze / thaw the card-store WRITERS across the fleet, reversibly.
#
# One-shot cutover tooling for
# cards-quiesce-state-refuses-writes-during-cutover-20260731. Deliberately a
# throwaway script in the scratchpad rather than a CLI verb: a migration that
# happens once does not earn a permanent surface.
#
# WHY SHELL AND NOT PYTHON. The job is "run systemctl over ssh", which is
# shell's native domain. The Python draft bought nothing but a JSON state file
# and dragged in a provenance/CONFIG/RNG decorator meant for data-lineage
# scripts. Fewer moving parts, and it runs on any host without a venv.
#
# ── WHAT IT WILL NOT TOUCH, which is the whole safety story ──────────────────
# scitex-cards-pg.service is the PostgreSQL server holding the board. It must
# stay UP or there is nothing to pg_dump and the migration has no input. Its
# name shares the `scitex-cards-` prefix with every writer, so the obvious
#     systemctl --user stop 'scitex-cards-*'
# would take the database down with the writers. That glob is exactly why this
# script names units EXPLICITLY and re-checks the deny-list immediately before
# each stop rather than trusting the plan it built a moment earlier.
#
# ── ORDER, measured not guessed ──────────────────────────────────────────────
# scitex-cards-sync-peers.timer exists on compute-04 ONLY: cross-host
# propagation is hub-and-spoke, not a mesh, and the sync is ADDITIVE-ONLY. It
# is stopped FIRST. Stopping it last would let it re-import rows from hosts
# already quiesced, so the last host stopped would repopulate the first.
#
# ── REVERSAL IS FROM RECORDED STATE, NOT FROM THIS LIST ──────────────────────
# --freeze records each unit it actually stopped; --thaw restarts only those.
# Restarting "everything in the plan" would start units that were already dead
# for their own reasons, turning a reversal into a change. Without the record
# --thaw refuses rather than guessing.

set -uo pipefail

# THE THAW RECORD MUST OUTLIVE THIS MACHINE'S /tmp.
#
# It answers "what must be restarted", and that answer must be the same for
# anyone performing the restore — which is precisely the state the operator's
# 2026-08-17 ruling disqualifies from living in a private file: 「データベースを
# 使わないで状態を表しているファイルがあるならばそれは失格です」.
#
# It is not hypothetical here. On 2026-08-20 an accidental --freeze wrote this
# record and my own cleanup `rm -f` deleted it in the same command, so --thaw
# had nothing to restore from; the two stopped services were recovered only
# because their names were still on screen. A record that dies with the shell
# that wrote it is not a record.
#
# So: keep the local file as the operational cursor (cheap, no dependency mid
# window), and MIRROR every freeze to the card as a comment, which lives in
# PostgreSQL 55432 and is readable by any agent on any host. `--freeze` prints
# the exact mirror command; run it, do not rely on this file surviving.
STATE="${CUTOVER_STATE:-$HOME/.scitex/cards/runtime/cutover_writers_state.tsv}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
RUNTIME='export XDG_RUNTIME_DIR=/run/user/$(id -u);'

# NEVER stopped, at any stage, on any host.
is_protected() {
    case "$1" in
        scitex-cards-pg.service | scitex-cards-pg-alert.service) return 0 ;;
        *) return 1 ;;
    esac
}

# host:unit,unit,...  — order within a host is the stop order.
#
# ywata-note-win IS LISTED DELIBERATELY, THOUGH IT IS CURRENTLY UNREACHABLE.
# Omitting it would have been the worse bug: --dry-run would print four healthy
# hosts and read as full fleet coverage, so the one host nobody can stop would
# be the one host nobody SEES. An enumeration that excludes the problem case
# reports "all clear" for the wrong reason. Listed, it shows UNREACHABLE and
# verdict=skip, which is the honest answer.
#
# It also bounds a claim I overstated on the card: "sync-peers runs on
# compute-04 ONLY" was measured across the four REACHABLE hosts. Whether
# ywata-note-win also runs a sync timer is UNMEASURED, so "hub-and-spoke, not a
# mesh" holds for four of five hosts and is a hypothesis for the fifth.
PLAN=(
    "scitex-compute-01:scitex-dev-ecosystem.service"
    "scitex-compute-02:scitex-dev-ecosystem.service"
    "scitex-compute-03:scitex-dev-ecosystem.service"
    "ywata-note-win:scitex-cards-sync-peers.timer,scitex-dev-ecosystem.service,scitex-cards-notifyd.service"
    "scitex-compute-04:scitex-cards-sync-peers.timer,scitex-cards-sync-peers.service,scitex-dev-ecosystem.service,scitex-cards-notifyd.service,scitex-cards-gui.service"
)

state_of() { # host unit -> active|inactive|failed|UNREACHABLE
    local out
    if ! out=$("${SSH[@]}" "$1" "$RUNTIME systemctl --user is-active $2" 2>&1); then
        case "$out" in
            *"No route"* | *"timed out"* | *handshake* | *"Connection closed"*)
                echo UNREACHABLE
                return
                ;;
        esac
    fi
    echo "${out:-unknown}" | tail -1 | tr -d '[:space:]'
}

survey() { # prints: host<TAB>unit<TAB>state<TAB>verdict
    local entry host units unit st verdict
    for entry in "${PLAN[@]}"; do
        host="${entry%%:*}"
        units="${entry#*:}"
        for unit in ${units//,/ }; do
            if is_protected "$unit"; then
                printf '%s\t%s\t%s\tREFUSED-PROTECTED\n' "$host" "$unit" "-"
                continue
            fi
            st=$(state_of "$host" "$unit")
            verdict=skip
            [[ $st == active ]] && verdict=STOP
            printf '%s\t%s\t%s\t%s\n' "$host" "$unit" "$st" "$verdict"
        done
    done
}

cmd_dry_run() {
    echo "=== PLAN (nothing is being stopped) ==="
    survey | awk -F'\t' '{printf "  [%-4s] %-22s %-38s currently=%s\n",$4,$1,$2,$3}'
    echo
    echo "PROTECTED, never stopped: scitex-cards-pg.service scitex-cards-pg-alert.service"
}

cmd_freeze() {
    # THE CONFIRMATION GATE, added after --freeze fired by accident.
    #
    # 2026-08-20: I ran `--freeze` on a modified copy as a "positive control"
    # for the deny-list, expecting a protected unit in the plan to ABORT the
    # run. It does not, and should not: the deny-list refuses THAT UNIT and
    # correctly proceeds with the rest. So the control stopped two live
    # scitex-dev-ecosystem services on two hosts. They were restarted within
    # the minute and the fleet verified healthy, but nothing about the script
    # made the mutation hard to trigger.
    #
    # The read-only --dry-run had ALREADY printed REFUSED-PROTECTED one line
    # earlier -- the question was answered before the mutating verb ran. When a
    # read and a write both settle a question, the read cannot change the
    # subject, so the write is not a second opinion; it is only a risk.
    #
    # Hence a token that cannot be typed by momentum. --dry-run stays free.
    if [[ ${CUTOVER_I_MEAN_IT:-} != "yes" ]]; then
        echo "REFUSING: --freeze stops LIVE fleet services." >&2
        echo "Re-run with CUTOVER_I_MEAN_IT=yes if that is genuinely intended." >&2
        echo "To inspect without mutating anything, use --dry-run." >&2
        exit 1
    fi
    [[ -e $STATE ]] && {
        echo "REFUSING: $STATE exists — thaw first, or move it aside." >&2
        exit 1
    }
    local host unit st verdict n=0
    : >"$STATE"
    while IFS=$'\t' read -r host unit st verdict; do
        [[ $verdict == STOP ]] || continue
        is_protected "$unit" && {
            echo "REFUSING: $unit is protected" >&2
            exit 1
        }
        if "${SSH[@]}" "$host" "$RUNTIME systemctl --user stop $unit" 2>&1; then
            printf '%s\t%s\n' "$host" "$unit" >>"$STATE"
            echo "  stopped $host $unit"
            n=$((n + 1))
        else
            echo "  FAILED  $host $unit" >&2
        fi
    done < <(survey)
    echo "recorded $n stop(s) -> $STATE"
    mirror_to_card "$n"
}

CARD=cards-quiesce-state-refuses-writes-during-cutover-20260731

mirror_to_card() { # THE MIRROR IS PERFORMED, NOT SUGGESTED.
    # An earlier version of this function PRINTED the command for a human to
    # run. That is a written warning standing in for a mechanical barrier, and
    # a printed instruction during a live cutover is read at exactly the moment
    # nobody is reading. So the freeze writes the record itself.
    #
    # ORDER: local cursor FIRST (already written by the caller), durable record
    # SECOND. Never the reverse — if the card write blocks or the store is
    # mid-restart, --thaw must still have something local to work from.
    #
    # A FAILED MIRROR DOES NOT UNDO THE FREEZE, and must not: the services are
    # already stopped, and refusing after the fact would leave the fleet down
    # with no record at all. It fails LOUD instead, printing the fallback, so
    # "the record is missing" can never be silent.
    local n="$1" body
    body="FREEZE $(date -u +%FT%TZ) on $(hostname) — stopped ${n} unit(s):
$(cat "$STATE")

Restore with: cutover_writers.sh --thaw  (state: $STATE)"
    # POSITIONAL TEXT, not --text. Verified against `scitex-cards comment
    # --help` on the host rather than assumed: the signature is
    # `comment [OPTIONS] TASK_ID TEXT`. The --text form I first wrote would
    # have failed on EVERY invocation and dropped silently into the loud-fail
    # branch below — a mirror that could never once succeed, which is the
    # gate-that-cannot-fire in its most flattering disguise, because the
    # fallback text would have made it look careful.
    if command -v scitex-cards >/dev/null 2>&1 &&
        scitex-cards comment "$CARD" "$body" >/dev/null 2>&1; then
        echo "mirrored the freeze record to card $CARD"
        return 0
    fi
    echo "!! MIRROR FAILED — the durable record was NOT written." >&2
    echo "!! The local cursor at $STATE is now the ONLY record of what to restart." >&2
    echo "!! Copy this onto card $CARD by hand, now:" >&2
    printf '%s\n' "$body" >&2
    return 1
}

cmd_thaw() {
    [[ -e $STATE ]] || {
        echo "REFUSING: no $STATE. Thaw restarts only what THIS script stopped;" >&2
        echo "without the record it would start units already down for their own reasons." >&2
        exit 1
    }
    tac "$STATE" | while IFS=$'\t' read -r host unit; do
        if "${SSH[@]}" "$host" "$RUNTIME systemctl --user start $unit" 2>&1; then
            echo "  started $host $unit"
        else
            echo "  FAILED  $host $unit" >&2
        fi
    done
    mv "$STATE" "$STATE.done"
}

case "${1:-}" in
    --dry-run) cmd_dry_run ;;
    --freeze) cmd_freeze ;;
    --thaw) cmd_thaw ;;
    *)
        echo "usage: $0 --dry-run | --freeze | --thaw" >&2
        exit 2
        ;;
esac
