#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the off-site backup still happening? Answered from its OUTPUT.

WHY THIS EXISTS. The hourly snapshot rail died silently for 18 HOURS when a
crontab reconcile swept its predecessor — 188 cards of change unbacked. It was
found because a peer hand-read the snapshot repo's commit log, not by any
alarm of ours. Moving the rail to a systemd user timer fixed THAT cause; it
added no detector, so the next cause (disk full, auth expiry, a bad release, a
remote outage) reproduces the same silence.

A backup that fails silently is WORSE than no backup: it buys false
confidence. We would have kept working believing the board was safe.

WHAT IT READS, AND WHY THAT PARTICULAR THING. The AGE OF THE NEWEST SNAPSHOT
COMMIT — nothing else. Two rules earned the hard way tonight decide this:

1. Prefer the instrument that measures the OUTPUT over the one that reports
   the mechanism's opinion of itself.
2. The artifact must be one that COULD NOT EXIST if the work had not happened.

A commit passes both. `systemctl --user show -p Result -p ExecMainStatus`
fails the first badly enough to be disqualifying: it returns
``Result=success ExecMainStatus=0 rc=0`` for a unit THAT DOES NOT EXIST,
because it answers with property defaults. An alarm built on it would report
healthy at the exact moment the rail was deleted. Verified on this host.

Commit age also collapses every distinct failure into the one question that
matters. Timer deleted, timer wedged, push rejected, disk full, git broken —
all of them stop producing commits, and none of them can fake one.

TERNARY, NEVER BINARY. ``fresh`` / ``stale`` / ``unknown``. "I could not
determine" is a first-class answer with its own reason, never quietly folded
into either pole — collapsing UNKNOWN is the single defect behind most of the
fleet's recent incidents. A missing snapshot REPO means unknown, not stale:
its absence has causes (never provisioned, wrong path) unrelated to the rail
having stopped, so calling it stale would be a guess wearing a verdict's
clothes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

#: Default tolerance. The rail fires hourly, so ~2 intervals plus slack keeps
#: one skipped or slow run from crying wolf while still catching a real stop
#: within a couple of hours. A backup alarm that fires spuriously gets muted,
#: and a muted alarm is the failure it was built to prevent.
DEFAULT_MAX_AGE_SECONDS = 3 * 3600


def default_snapshot_dir(db_dir: str | Path | None = None) -> Path:
    """Where ``db snapshot`` keeps its git repo."""
    base = Path(db_dir).expanduser() if db_dir else Path.home() / ".scitex" / "cards"
    return base / "snapshots"


def newest_commit_epoch(snapshot_dir: str | Path | None = None) -> int | None:
    """Unix time of the newest snapshot commit, or None if undeterminable.

    None means UNKNOWN — no repo, no commits, git unavailable, a path that is
    not a repository. Never 0, never "very old": a sentinel that sorts as
    ancient would render UNKNOWN as STALE, which is the collapse this module
    exists to refuse.
    """
    root = Path(snapshot_dir) if snapshot_dir else default_snapshot_dir()
    if not (root / ".git").exists():
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except Exception:  # noqa: BLE001 — git missing/hung is UNKNOWN, not stale
        return None
    if done.returncode != 0:
        return None
    raw = (done.stdout or "").strip()
    return int(raw) if raw.isdigit() else None


def assess(
    snapshot_dir: str | Path | None = None,
    *,
    now: int | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Verdict on the backup rail's liveness, judged by its output.

    Returns the fleet-standard check shape — ``{name, ok, state, detail,
    hint}`` — where ``ok`` is False for BOTH ``stale`` and ``unknown``. An
    undeterminable backup is not a passing backup: the whole point is that
    nobody should be able to read this as healthy without evidence.
    """
    import time as _time

    root = Path(snapshot_dir) if snapshot_dir else default_snapshot_dir()
    now = int(now if now is not None else _time.time())
    epoch = newest_commit_epoch(root)

    if epoch is None:
        return {
            "name": "snapshot_freshness",
            "ok": False,
            "state": "unknown",
            "age_seconds": None,
            "detail": f"cannot determine the last snapshot from {root}",
            "hint": (
                "Not the same as 'the backup stopped' — the repo may never "
                "have been provisioned, or the path may be wrong. Check that "
                f"{root} is a git repo with commits, then re-check. Do not "
                "treat this as healthy."
            ),
        }

    age = now - epoch
    if age <= max_age_seconds:
        return {
            "name": "snapshot_freshness",
            "ok": True,
            "state": "fresh",
            "age_seconds": age,
            "detail": f"last snapshot {age}s ago (limit {max_age_seconds}s)",
            "hint": "",
        }

    return {
        "name": "snapshot_freshness",
        "ok": False,
        "state": "stale",
        "age_seconds": age,
        "detail": (
            f"last snapshot was {age}s ago, over the {max_age_seconds}s limit "
            f"— the off-site backup has STOPPED producing commits"
        ),
        "hint": (
            "The rail is silent. Check the timer exists at all with "
            "`systemctl --user is-enabled scitex-cards-snapshot.timer` (NOT "
            "`show -p Result`, which reports success for a unit that does not "
            "exist), then run `scitex-cards db snapshot --refresh --push` by "
            "hand and read the error. A push failure exits 1 by design."
        ),
    }


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "assess",
    "default_snapshot_dir",
    "newest_commit_epoch",
]

# EOF
