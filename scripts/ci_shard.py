#!/usr/bin/env python3
"""Deterministic, balanced CI sharding for the scitex-cards test suite.

The full suite is ~7,500 tests. Under ``-n auto`` (load) xdist, the
*slowest* tests cluster on the workers that happen to grab them, so one
worker finishes at minute ~30 while others are still going — the wall time
is the max-shard time, not the average. This module replaces that with
**deterministic, balanced, duration-aware** shards so that:

  * every test node-id is selected by exactly one shard (anti-vacuity is
    verified, not assumed), and
  * each shard's estimated total duration is within a bounded factor of the
    mean, so wall time tracks the average, not the worst-case pile-up.

Sharding strategy (cheapest that is provably balanced first):

  1. **Duration-aware LPT (Longest-Processing-Time first):** if a per-test-id
     duration profile is available (``--profile profile.json``), sort tests
     descending and assign each to the currently-lightest shard. This is the
     classic makespan-minimizing list schedule and is deterministic for a
     fixed (profile, shard-count, node-id sort order).
  2. **DB-heavy / DB-free + file-count split (fallback):** if no profile,
     bucket test *files* by DB-touching vs DB-free, then bin-pack files
     across shards to balance test count. DB files are the long tail, so this
     alone removes most of the load imbalance; a profile upgrades it to
     true duration balance.

The module is pure (no pytest plugin, no DB, no network) so it can be unit-
tested hermetically and also driven by the release workflow to emit per-shard
node-id lists + a coverage manifest that the aggregate gate re-checks.

DB isolation is a *pytest/conftest* concern (each test carves its own
throwaway schema via ``ephemeral_schema``), not a sharding concern: a shard
is just a subset of node-ids run in its own pytest process, so the existing
per-test schema isolation applies unchanged. This tool never merges,
skips, xfails, or reorders the *semantics* of any test — only which
node-ids go into which worker.

Usage (from the repo root):
    python scripts/ci_shard.py --shards 4 --out /tmp/shards
    python scripts/ci_shard.py --shards 4 --profile .ci_duration.json --out /tmp/shards
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Sequence, Set, Tuple


# ---------------------------------------------------------------------------
# Test collection (offline, deterministic)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShardPlan:
    """A concrete sharding decision: node-id -> shard index, plus the manifest."""

    shards: Tuple[Tuple[str, ...], ...]   # shards[i] = tuple of node-ids
    profile_ms: Tuple[float, ...]         # estimated ms per shard (balance evidence)
    max_deviation: float                   # (max - mean)/mean across shards, 0 == perfect
    total_tests: int
    anti_vacuity_ok: bool                  # every node-id in exactly one shard

    def write(self, out_dir: str) -> List[str]:
        os.makedirs(out_dir, exist_ok=True)
        paths: List[str] = []
        for i, nodes in enumerate(self.shards):
            p = os.path.join(out_dir, f"shard_{i:02d}.txt")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n".join(nodes) + ("\n" if nodes else ""))
            paths.append(p)
        manifest = {
            "shard_count": len(self.shards),
            "total_tests": self.total_tests,
            "per_shard_counts": [len(s) for s in self.shards],
            "profile_ms": [round(m, 1) for m in self.profile_ms],
            "max_deviation_from_mean": round(self.max_deviation, 4),
            "anti_vacuity_ok": self.anti_vacuity_ok,
        }
        mp = os.path.join(out_dir, "manifest.json")
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
        return paths + [mp]


def _node_ids_from_collect(collect_out: str) -> List[str]:
    """Parse a ``pytest --collect-only -q`` output into ordered node-ids.

    ``-q`` collect emits one node-id per line; the trailing summary
    (``N tests collected in Xs``) and any ``<...>`` warning lines are not
    node-ids and are dropped.
    """
    out: List[str] = []
    for line in collect_out.splitlines():
        line = line.strip()
        if not line:
            continue
        # A pytest node-id always contains '::' OR is a bare .py path that was
        # collected. Summary/warning lines do not end in a test selector.
        if "::" in line or re.search(r"\.py(:|$)", line):
            # guard against the "N tests collected" summary
            if re.fullmatch(r"\d+ (tests?|item) collected in [0-9.]+s", line):
                continue
            out.append(line)
    return out


def _file_of(node_id: str) -> str:
    """The test-file portion of a node-id (``path::cls::test`` -> ``path``)."""
    return node_id.split("::", 1)[0]


# ---------------------------------------------------------------------------
# DB-heavy detection (the cheap, profile-free balancing signal)
# ---------------------------------------------------------------------------

# Markers that indicate a test *file* drives a real Postgres store.
# Kept in sync with tests/conftest.py's throwaway-schema machinery.
_DB_FILE_MARKERS = (
    "new_store",
    "ephemeral_schema",
    "writable_dsn",
    "SCITEX_CARDS_DB",
    "postgres_dsn",
)

# A coarse weight for a DB file vs a DB-free file when no duration profile is
# supplied. DB-backed tests dominate the wall time, so they carry most of the
# cost. This is only a tie-breaker signal for the file-count bin-packer.
_DB_FILE_WEIGHT = 8.0
_NODB_FILE_WEIGHT = 1.0


def db_file_flags(file_contents: Dict[str, str]) -> Dict[str, bool]:
    """Map test-file -> True if the file's source references the DB markers."""
    flags: Dict[str, bool] = {}
    for path, src in file_contents.items():
        flags[path] = any(m in src for m in _DB_FILE_MARKERS)
    return flags


# ---------------------------------------------------------------------------
# Balancers
# ---------------------------------------------------------------------------

def _lpt_assign(node_ids: Sequence[str], weight: Dict[str, float],
                n_shards: int) -> List[List[str]]:
    """Duration-aware LPT: longest first into the currently-lightest shard.

    Deterministic: input is sorted by (-weight, node_id); ties are broken by
    node-id string so the same profile + shard count always yields the same
    plan.
    """
    order = sorted(node_ids, key=lambda nid: (-weight.get(nid, 0.0), nid))
    shards: List[List[str]] = [[] for _ in range(n_shards)]
    load: List[float] = [0.0] * n_shards
    for nid in order:
        # pick the lightest shard; on ties, lowest index (deterministic)
        k = min(range(n_shards), key=lambda i: (load[i], i))
        shards[k].append(nid)
        load[k] += weight.get(nid, 0.0)
    # sort each shard's node-ids back to stable (original) order for output
    stable = {nid: i for i, nid in enumerate(node_ids)}
    for s in shards:
        s.sort(key=lambda nid: stable[nid])
    return shards


def _file_count_binpack(node_ids: Sequence[str],
                        file_db: Dict[str, bool],
                        n_shards: int) -> List[List[str]]:
    """Profile-free fallback: bin-pack whole files across shards.

    Group node-ids by file, weight each file by (DB weight) * (test count),
    then LPT-assign *files* (keeps all of a file on one worker, which also
    keeps a file's throwaway-schema setup from being split). Deterministic.
    """
    by_file: Dict[str, List[str]] = {}
    for nid in node_ids:
        by_file.setdefault(_file_of(nid), []).append(nid)
    # LPT on files
    file_order = sorted(
        by_file,
        key=lambda f: (-(1 if file_db.get(f) else 0)) * _DB_FILE_WEIGHT - len(by_file[f]) * _NODB_FILE_WEIGHT,
    )
    shards: List[List[str]] = [[] for _ in range(n_shards)]
    load: List[float] = [0.0] * n_shards
    for f in file_order:
        w = (_DB_FILE_WEIGHT if file_db.get(f) else _NODB_FILE_WEIGHT) * len(by_file[f])
        k = min(range(n_shards), key=lambda i: (load[i], i))
        shards[k].extend(by_file[f])
        load[k] += w
    stable = {nid: i for i, nid in enumerate(node_ids)}
    for s in shards:
        s.sort(key=lambda nid: stable[nid])
    return shards


def _balance_stats(shards: Sequence[Sequence[str]],
                   weight: Dict[str, float]) -> Tuple[float, ...]:
    ms = [sum(weight.get(nid, 0.0) for nid in s) for s in shards]
    return tuple(ms)


def max_deviation_from_mean(profile_ms: Sequence[float]) -> float:
    """(max - mean)/mean; 0.0 == perfectly balanced."""
    n = len(profile_ms)
    if n == 0:
        return 0.0
    mean = sum(profile_ms) / n
    if mean == 0:
        # all weights zero: balance is trivially fine
        return 0.0
    return (max(profile_ms) - mean) / mean


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------

def verify_anti_vacuity(full: Sequence[str],
                        shards: Sequence[Sequence[str]]) -> bool:
    """Every full node-id appears in exactly one shard; no shard has extras."""
    seen: Dict[str, int] = {}
    for s in shards:
        for nid in s:
            seen[nid] = seen.get(nid, 0) + 1
    full_set: Set[str] = set(full)
    # no duplicates within or across shards
    for nid, c in seen.items():
        if c != 1:
            return False
    # shard union == full set (nothing missing, nothing extra)
    if set(seen.keys()) != full_set:
        return False
    return True


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profile(path: str) -> Dict[str, float]:
    """node-id -> estimated duration (ms). Tolerant of missing node-ids."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    # accept either {node_id: ms} or {"durations": {node_id: ms}}
    if isinstance(raw, dict) and "durations" in raw and isinstance(raw["durations"], dict):
        raw = raw["durations"]
    return {str(k): float(v) for k, v in raw.items() if k}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_plan(node_ids: Sequence[str],
               n_shards: int,
               profile: Dict[str, float] | None,
               file_db: Dict[str, bool] | None,
               ) -> ShardPlan:
    node_ids = list(dict.fromkeys(node_ids))  # de-dupe, preserve order
    if n_shards <= 0:
        raise ValueError("n_shards must be >= 1")
    if not node_ids:
        raise ValueError("no node-ids collected — refusing to emit a vacuous plan")

    if profile:
        # duration-aware LPT. Tests with no profile entry get weight 0 (they
        # still get placed, just early in the tie-break); a full profile makes
        # this optimal list-schedule.
        weight = {nid: profile.get(nid, 0.0) for nid in node_ids}
        shards = _lpt_assign(node_ids, weight, n_shards)
        ms = _balance_stats(shards, weight)
    else:
        # profile-free: DB/file-count bin-pack. Report a *count-based* ms so the
        # deviation stat still has meaning (1 unit == 1 test, DB files weighted).
        weight = {}
        for nid in node_ids:
            f = _file_of(nid)
            weight[nid] = _DB_FILE_WEIGHT if (file_db or {}).get(f) else _NODB_FILE_WEIGHT
        shards = _file_count_binpack(node_ids, file_db or {}, n_shards)
        ms = _balance_stats(shards, weight)

    ok = verify_anti_vacuity(node_ids, shards)
    return ShardPlan(
        shards=tuple(tuple(s) for s in shards),
        profile_ms=tuple(ms),
        max_deviation=max_deviation_from_mean(ms),
        total_tests=len(node_ids),
        anti_vacuity_ok=ok,
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shards", type=int, default=4, help="number of shards (== xdist -n)")
    ap.add_argument("--profile", default=None, help="path to node-id->ms duration JSON")
    ap.add_argument("--out", default="./shards", help="output dir for shard files + manifest")
    ap.add_argument("--collect-cmd", default=None,
                    help="optional: shell cmd that emits node-ids (else --node-ids-file / stdin)")
    ap.add_argument("--node-ids-file", default=None, help="file with one node-id per line")
    ap.add_argument("--files-dir", default="tests",
                    help="dir to scan for test-file DB markers (profile-free mode)")
    ap.add_argument("--fail-on-imbalance", type=float, default=None,
                    help="exit 1 if max_deviation_from_mean exceeds this (CI guard)")
    args = ap.parse_args(argv)

    # 1) node-ids
    if args.node_ids_file:
        with open(args.node_ids_file, encoding="utf-8") as fh:
            node_ids = [ln.strip() for ln in fh if ln.strip()]
    elif args.collect_cmd:
        import subprocess
        cp = subprocess.run(args.collect_cmd, shell=True, capture_output=True, text=True)
        if cp.returncode != 0:
            print(cp.stderr, file=sys.stderr)
            return 3
        node_ids = _node_ids_from_collect(cp.stdout)
    else:
        node_ids = [ln.strip() for ln in sys.stdin if ln.strip()]
    node_ids = [n for n in node_ids if n]

    # 2) optional profile
    profile = load_profile(args.profile) if args.profile else None

    # 3) optional file DB flags (profile-free mode)
    file_db: Dict[str, bool] | None = None
    if not profile and args.files_dir and os.path.isdir(args.files_dir):
        file_db = {}
        for root, _dirs, files in os.walk(args.files_dir):
            for fn in files:
                if fn.startswith("test_") and fn.endswith(".py"):
                    p = os.path.join(root, fn)
                    try:
                        with open(p, encoding="utf-8", errors="replace") as fh:
                            file_db[p] = any(m in fh.read() for m in _DB_FILE_MARKERS)
                    except OSError:
                        file_db[p] = False
    # node-ids use repo-relative file paths; align the DB flags keys
    if file_db:
        rel_flags = {}
        for k, v in file_db.items():
            rel_flags[k.replace(os.sep, "/")] = v
            # also the 'tests/...' prefix form and the bare form
            rel_flags[k] = v
        file_db = rel_flags

    plan = build_plan(node_ids, args.shards, profile, file_db)
    paths = plan.write(args.out)

    print(json.dumps({
        "shards": plan.shards and len(plan.shards),
        "total_tests": plan.total_tests,
        "per_shard_counts": [len(s) for s in plan.shards],
        "profile_ms": [round(m, 1) for m in plan.profile_ms],
        "max_deviation_from_mean": round(plan.max_deviation, 4),
        "anti_vacuity_ok": plan.anti_vacuity_ok,
        "out": [os.path.relpath(p) for p in paths],
    }, indent=2))

    if not plan.anti_vacuity_ok:
        print("ANTI-VACUITY FAILED: node-ids are not partitioned exactly once", file=sys.stderr)
        return 4
    if args.fail_on_imbalance is not None and plan.max_deviation > args.fail_on_imbalance:
        print(f"IMBALANCE FAIL: deviation {plan.max_deviation:.3f} > {args.fail_on_imbalance}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
