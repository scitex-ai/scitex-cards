#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo watch-ci` — server-side CI poller (record-only).

Renamed from `ci-watch` in the slice-6b verb-rename pilot (doctrine §1d:
compounds are kebab-case and VERB-FIRST); the old name stays as a hidden
warn-phase deprecated alias until v0.9.

Lead a2a (operator decoupled-pollers override, dev msg `96afacc7`,
2026-06-15): each server polls GitHub CI INDEPENDENTLY + dedupes its
own state. todo's lane = RECORD (the CI-pills strip + the per-repo
state cache); SAC's lane = DELIVERY (a2a verdict to owner +
lineage). Neither depends on the other; either can crash without
breaking the other.

This module is todo's lane.

## What it does

For each repo configured in
``~/.scitex/cards/dashboard.json`` (``fleet.ci_status.repos``) — or
the env override ``SCITEX_TODO_FLEET_CI_REPOS=owner/name,...``:

1. Call the existing
   ``_django.handlers.fleet.gh_ci.fetch_repo_ci_status`` adapter
   (the SAME source the FE-driven ``/fleet/ci-status`` endpoint uses
   — ONE source of truth for the CI state).
2. Diff the result against the local state cache at
   ``~/.scitex/todo/ci-state.json`` keyed by repo slug. The dedupe
   key shape matches the spec dev locked: ``(repo, head_sha,
   overall)``.
3. Classify the transition (``first-seen`` / ``newly-green`` /
   ``newly-red`` / ``still-pending`` / ``unchanged``) and log one
   stderr line per repo.
4. Update the cache, save atomically.

## What it does NOT do

- No event emission on the ``scitex_cards.hooks`` bus for the
  ``ci-result`` kind. Operator's decoupled-pollers override killed
  that path; SAC has its own independent poller for delivery.
- No a2a sends. todo records; SAC delivers. Each STANDALONE.

## Designed for cron use

JobSpec entry ``scitex-todo.ci-watch`` (registered via
``_jobs_provider.py``; the JobSpec NAME is a registry identity and
keeps its historical spelling) runs ``scitex-todo watch-ci --once`` every
5 min via ``scitex-dev ecosystem up``. The ``--once`` flag exits
after one sweep; absence of it loops with a configurable interval
(default 300s). Per the operator's principle: SAC + todo poll at
different cadences so the gh API isn't double-loaded.

Failure isolation: a per-repo adapter failure is reported on
stderr + skipped; the sweep continues. State for the failed repo is
NOT updated (so the next sweep retries). One bad repo doesn't
parkthe whole pipe.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click

from ._compat import deprecated_alias, spec_command_kwargs


def register(main: click.Group) -> None:
    """Attach the ``watch-ci`` verb (+ hidden warn-phase ``ci-watch`` alias).

    Renamed in the slice-6b verb-rename pilot: ``ci-watch`` was a
    noun-first compound; doctrine §1d grammar requires kebab-case
    VERB-FIRST compounds (``watch-ci``). The old name forwards with a
    once-per-shell stderr warning until v0.9.
    """
    main.add_command(watch_ci_cmd, name="watch-ci")
    deprecated_alias(main, "ci-watch", target="watch-ci", remove_in="0.9")


#: Status values that mean "we have a definitive verdict."
_DEFINITIVE = frozenset({"success", "failure"})


def classify_transition(
    prior: dict[str, Any] | None,
    current: dict[str, Any],
) -> str:
    """Classify the state-change between ``prior`` and ``current``.

    Returns one of:

      - ``first-seen``    — the repo wasn't in the state cache.
      - ``newly-green``   — was failure/pending/unknown; now success.
      - ``newly-red``     — was success/pending/unknown; now failure.
      - ``still-pending`` — neither prior nor current is definitive.
      - ``unchanged``     — same (head_sha, overall) tuple as before.

    The classification is a PURE FUNCTION of two dicts. The dedupe key
    is ``(head_sha, overall)`` per dev's locked spec (msg
    ``96afacc7``).
    """
    cur_overall = current.get("overall") or "unknown"
    cur_head = current.get("head_sha") or ""
    if prior is None:
        return "first-seen"
    prior_overall = prior.get("overall") or "unknown"
    prior_head = prior.get("head_sha") or ""
    # Definitive verdict landed (current is the FIRST sweep to see it).
    if cur_overall == "success" and prior_overall != "success":
        return "newly-green"
    if cur_overall == "failure" and prior_overall != "failure":
        return "newly-red"
    # Neither side definitive — still waiting, regardless of head_sha
    # drift. Operator-clarity choice (vs. "unchanged"): the cron tick
    # log should say WHY there's no new verdict, not just that nothing
    # moved. "still-pending" is the more informative label.
    if cur_overall not in _DEFINITIVE and prior_overall not in _DEFINITIVE:
        return "still-pending"
    # Same definitive verdict; head_sha may differ (force-push, etc.)
    # but the overall verdict is unchanged from the operator's POV.
    return "unchanged"


def state_path() -> Path:
    """Resolve the per-repo state cache path (env > home default)."""
    override = os.environ.get("SCITEX_TODO_CI_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".scitex" / "todo" / "ci-state.json"


def load_state(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the per-repo state cache; empty dict when absent / unreadable.

    Unreadable cache = empty dict, NOT a raise — the operator must be
    able to delete the cache to force a clean re-poll without crashing
    the cron.
    """
    p = path or state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        slug: entry
        for slug, entry in data.items()
        if isinstance(slug, str) and isinstance(entry, dict)
    }


def save_state(state: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    """Atomic save of the per-repo state cache (tmp + rename)."""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, p)


@click.command(
    "watch-ci",
    **spec_command_kwargs(
        summary="Server-side CI poller (record-only, decoupled-pollers lane).",
        description=(
            "Polls every configured repo's GitHub CI default-branch state, "
            "compares to the local state cache at "
            "~/.scitex/todo/ci-state.json (override via env "
            "SCITEX_TODO_CI_STATE), and logs the transition.\n\n"
            "Designed for cron use: --once runs ONE sweep + exits 0; "
            "absence of --once loops with --interval (default 300s)."
        ),
        examples=(
            ("{prog} watch-ci --once", ""),
            ("{prog} watch-ci --interval 600", ""),
            (
                "SCITEX_TODO_FLEET_CI_REPOS=owner/a,owner/b {prog} watch-ci --once",
                "",
            ),
        ),
    ),
)
@click.option(
    "--once",
    is_flag=True,
    help="Run ONE sweep then exit 0 (cron mode). Skip the loop.",
)
@click.option(
    "--interval",
    type=int,
    default=300,
    show_default=True,
    help="Loop interval in seconds. Ignored when --once is set.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned per-repo transition + summary without writing the state cache.",
)
def watch_ci_cmd(once: bool, interval: int, dry_run: bool) -> None:
    """Poll GH CI for every configured repo + record transitions."""
    while True:
        result = _run_one_sweep(dry_run=dry_run)
        if once:
            sys.exit(0 if result["errors"] == 0 else 1)
        # Loop mode — sleep, then sweep again.
        time.sleep(max(int(interval), 30))


def _run_one_sweep(*, dry_run: bool) -> dict[str, int]:
    """One pass: load config, load state, poll each repo, log, save.

    Returns a small summary dict for the caller's exit-code decision.
    """
    from .._django.handlers.fleet._config import fleet_config_load
    from .._django.handlers.fleet._errors import FleetAdapterError
    from .._django.handlers.fleet.gh_ci import fetch_repo_ci_status

    try:
        cfg = fleet_config_load()
    except Exception as exc:  # noqa: BLE001
        click.echo(f"# watch-ci: config load failed: {exc}", err=True)
        return {"agents": 0, "errors": 1, "transitions": 0}
    repos = list(((cfg.get("fleet") or {}).get("ci_status") or {}).get("repos") or [])
    state = load_state()
    transitions = 0
    errors = 0
    for slug in repos:
        if not isinstance(slug, str) or "/" not in slug:
            click.echo(f"# watch-ci: skipping invalid slug {slug!r}", err=True)
            continue
        try:
            current = fetch_repo_ci_status(slug)
        except FleetAdapterError as exc:
            click.echo(f"# watch-ci: {slug} adapter error: {exc}", err=True)
            errors += 1
            continue
        prior = state.get(slug)
        label = classify_transition(prior, current)
        head_sha = (current.get("head_sha") or "")[:10]
        overall = current.get("overall") or "unknown"
        click.echo(
            f"# watch-ci: {slug} @ {head_sha} → {overall} ({label})",
            err=True,
        )
        if label != "unchanged":
            transitions += 1
            state[slug] = {
                "head_sha": current.get("head_sha") or "",
                "overall": overall,
                "branch": current.get("branch") or "",
                "last_seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_transition": label,
            }
    if not dry_run and transitions:
        try:
            save_state(state)
        except OSError as exc:
            click.echo(f"# watch-ci: state save failed: {exc}", err=True)
            errors += 1
    click.echo(
        f"# watch-ci: repos={len(repos)} transitions={transitions} "
        f"errors={errors} dry_run={dry_run}",
        err=True,
    )
    return {"agents": len(repos), "errors": errors, "transitions": transitions}


# EOF
