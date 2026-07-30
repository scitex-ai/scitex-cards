#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo sync-github`` CLI verb.

Permanent version of the lead's one-time GitHub→board sync (a2a
``7489ac3173fa4d7e9b2a870e30085b44`` 2026-06-12). Pulls today's (or
``--since``) merged PRs / closed issues across the org, matches each
against existing tasks (pr_url / fuzzy title-token overlap), and emits
done updates + new-task records.

Shares the aggregator in :mod:`scitex_cards._throughput` with the
``print-stats`` verb (sibling module ``_stats.py``) so the WIP-
validation gate, the board's compact `Δ delta` pill, and the
``print-stats --notify`` body all derive from one shared definition of
"open" / "stale" / "completed".

Extracted from ``_cli/_stats.py`` (which had grown past the 512-line
convention) into its own module — same split shape every other
verb-group in this package already uses. Pure extraction: no behavior
change. ``register()`` is called from ``_stats.py``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import click

from .._model import load_tasks
from .._paths import resolve_tasks_path
from ._compat import spec_command_kwargs


def _gh_merged_prs(since: str) -> list[dict]:
    """Fetch ``ywatanabe1989/*`` PRs merged in the ``--since`` window."""
    proc = subprocess.run(
        [
            "gh",
            "search",
            "prs",
            "--owner",
            "ywatanabe1989",
            "--merged",
            "--merged-at",
            f"{since}..*",
            "--limit",
            "300",
            "--json",
            "number,title,repository,author",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise click.ClickException(f"gh search prs failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def _project_for_repo(repo: str) -> str:
    """Map a GitHub repo name → board ``project`` field. The default is
    the repo name verbatim (matches the store's existing convention)."""
    # neurovista repo → paper-neurovista project (operator's title-prefix
    # convention puts "paper-" on cohort papers).
    overrides = {"neurovista": "paper-neurovista"}
    return overrides.get(repo, repo)


def _is_ci_speedup(title: str) -> bool:
    t = title.lower()
    return "ci-speedup" in t or "l1-l5" in t.lower()


@click.command(
    name="sync-github",
    **spec_command_kwargs(
        summary="Permanent GitHub -> board sync (merged PRs -> done tasks).",
        description=(
            "Pulls merged PRs across ywatanabe1989/* since the given "
            "date (default: today UTC). Matches each PR against "
            "existing tasks (pr_url first, then fuzzy title-token "
            "overlap within the same project). Done-flips matches; "
            "creates status=done records for unmatched PRs "
            "(project=repo, agent='lead'). Mechanical CI-speedup PRs "
            "(title contains 'ci-speedup' or 'L1-L5') collapse into a "
            "single bundle record per day. The aggregation function is "
            "shared with `print-stats` and the WIP gate via "
            "scitex_cards._throughput.",
        ),
        examples=(
            ("{prog} sync-github --dry-run", "Preview the planned actions."),
            ("{prog} sync-github --since 2026-06-01 -y", "Sync since a date."),
            ("{prog} sync-github -y", "Cron / scripted use."),
        ),
    ),
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601 date (YYYY-MM-DD). Defaults to today (UTC).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned actions without executing.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help=(
        "Skip the interactive confirmation. Required when the planned "
        "actions would mutate the store and stdin is a TTY; harmless on "
        "scripted / cron invocations (stdin not a TTY → auto-yes)."
    ),
)
def sync_github_cmd(since: str | None, dry_run: bool, assume_yes: bool) -> None:
    """Permanent GitHub→board sync.

    Pulls merged PRs across ``ywatanabe1989/*`` since the given date
    (default: today UTC). Matches each PR against existing tasks
    (``pr_url`` first, then fuzzy title-token overlap within the same
    project). Done-flips matches; creates ``status=done`` records for
    unmatched PRs (``project = repo``, ``agent = "lead"``). Mechanical
    CI-speedup PRs (title contains "ci-speedup" or "L1-L5") collapse
    into a single bundle record per day.

    The aggregation function is shared with ``stats`` and the WIP gate
    via :mod:`scitex_cards._throughput`.

    \b
    Example:
      $ scitex-todo sync-github --dry-run
      $ scitex-todo sync-github --since 2026-06-01 -y
      $ scitex-todo sync-github -y                  # cron / scripted use
    """
    # Defer the imports to avoid pulling `_store` at module load (some
    # call paths import this module without needing the write side).
    from .._store import add_task, update_task

    target_since = since or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Refuse-without-yes gate per audit §2 (mutating verbs must NOT
    # prompt interactively — they refuse and tell the operator what
    # flag to pass). Both --yes and --dry-run are explicit opt-ins;
    # bare invocation exits non-zero.
    if not dry_run and not assume_yes:
        raise click.ClickException(
            "sync-github mutates the store. Pass --yes / -y to confirm, "
            "or --dry-run to preview the planned actions."
        )
    path = resolve_tasks_path(None)
    tasks = load_tasks(path)
    by_pr_url = {t["pr_url"]: t for t in tasks if t.get("pr_url")}

    prs = _gh_merged_prs(target_since)
    ci_speedup = [p for p in prs if _is_ci_speedup(p["title"])]
    other = [p for p in prs if not _is_ci_speedup(p["title"])]

    actions_planned = 0
    done_updates = 0
    new_tasks = 0

    # --- (1) CI-speedup bundle ---------------------------------------------
    if ci_speedup:
        repos = sorted({p["repository"]["name"] for p in ci_speedup})
        bundle_id = f"ops-{target_since}-ci-speedup-wave"
        links = "\n".join(
            f"- {p['repository']['name']}#{p['number']}: {p['title']}"
            for p in ci_speedup
        )
        title = (
            f"CI-speedup wave (L1-L5) across {len(repos)} repos — merged {target_since}"
        )
        if bundle_id not in {t.get("id") for t in tasks}:
            click.echo(f"+ NEW  {bundle_id}  ({len(ci_speedup)} PRs)")
            actions_planned += 1
            new_tasks += 1
            if not dry_run:
                add_task(
                    str(path),
                    id=bundle_id,
                    title=title,
                    status="done",
                    project="scitex-dev",
                    # The owner must be a LIVE identity. Writing the dead
                    # `proj-` prefix here filed the card under an agent that
                    # does not exist, so its real owner never saw it.
                    agent="scitex-dev",
                    # add_task now REQUIRES a resolvable creator (no silent
                    # blank/"unknown"). This sync verb is a housekeeping
                    # importer, so it stamps itself as the creator.
                    created_by="sync-github",
                    note=(
                        "Consolidated record of the scitex-dev L1-L5 "
                        "CI-speedup template apply wave merged "
                        f"{target_since}. Individual PRs:\n{links}"
                    ),
                )

    # --- (2) Other PRs: match-or-create -----------------------------------
    for p in other:
        repo = p["repository"]["name"]
        n = p["number"]
        url = f"https://github.com/ywatanabe1989/{repo}/pull/{n}"
        title = p["title"]
        project = _project_for_repo(repo)
        agent = "lead"  # ywatanabe1989 → lead per the housekeeping convention.

        existing = by_pr_url.get(url)
        if existing is not None:
            if existing.get("status") != "done":
                click.echo(f"✓ DONE  {existing['id']}  {url}")
                actions_planned += 1
                done_updates += 1
                if not dry_run:
                    update_task(str(path), task_id=existing["id"], status="done")
            continue

        new_id = f"pr-{repo.replace('/', '-')}-{n}"
        if new_id in {t.get("id") for t in tasks}:
            # Already created in a prior sync — leave it.
            continue
        click.echo(f"+ NEW   {new_id}  ({repo}#{n})")
        actions_planned += 1
        new_tasks += 1
        if not dry_run:
            add_task(
                str(path),
                id=new_id,
                title=f"{repo}#{n}: {title}",
                status="done",
                project=project,
                agent=agent,
                # add_task now REQUIRES a resolvable creator — this verb is a
                # housekeeping importer, so it stamps itself.
                created_by="sync-github",
                pr_url=url,
            )

    click.echo("")
    click.echo(
        f"sync-github: {done_updates} done updates / {new_tasks} new tasks "
        f"({actions_planned} total actions, since={target_since}"
        f"{', dry-run' if dry_run else ''})"
    )


def register(group: click.Group) -> None:
    """Attach the `sync-github` verb to the root CLI."""
    group.add_command(sync_github_cmd)


__all__ = ["register", "sync_github_cmd"]
