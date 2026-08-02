#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI `list-stale` verb — terminal counterpart to the board's
`/stale` endpoint (PR #153) + the 🧹 Stale Review panel (PR #154).

Renamed from `stale-list` in the slice-6b verb-rename pilot (doctrine
§1d: compounds are kebab-case and VERB-FIRST); the old name stays as a
hidden warn-phase deprecated alias until v0.9.

The board UI surfaces the same data graphically. This verb is for
operators / agents working from a shell who want a one-line answer to
"what's stale on my board?" without opening the browser.

Criteria mirror the server-side derivation (kept in sync):
    - ``status == "deferred"`` (the backlog status; ``pending`` was abolished
      2026-07-10) AND
        - ``created_at > N days`` (default 14), OR
        - no ``created_at`` and no ``last_activity``, OR
        - title is empty/very-short AND there's no assignee / repo /
          project anchor (vague/orphaned heuristic).

If multiple criteria fire, all reasons are listed.

Provenance:
- Operator directive 2026-06-13 — recurring stale-review.
- HTTP twin: ``handlers/stale.py::handle_stale`` (PR #153).
"""

from __future__ import annotations

import datetime
import json

import click

from .._backlog_triage import BACKLOG_STATUS
from .._paths import resolve_tasks_path
from .._store import load_tasks
from ._compat import deprecated_alias, spec_command_kwargs
from ._write import _emit

_DEFAULT_DAYS = 14


def _parse_iso(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_unclear(task: dict) -> bool:
    title = (task.get("title") or "").strip()
    if not title or len(title) < 12:
        if not (
            task.get("assignee")
            or task.get("agent")
            or task.get("repo")
            or task.get("project")
        ):
            return True
    return False


def _stale_reasons(task: dict, now, cut) -> list[str]:
    reasons: list[str] = []
    created = _parse_iso(task.get("created_at"))
    last_act = _parse_iso(task.get("last_activity"))
    if created is not None and created < cut:
        reasons.append(f"created_at>{(now - cut).days}d ({created.date()})")
    elif last_act is not None and last_act < cut:
        reasons.append(f"last_activity>{(now - cut).days}d ({last_act.date()})")
    if created is None and last_act is None:
        reasons.append("no created_at + no last_activity")
    if _is_unclear(task):
        reasons.append("vague/orphaned (no clear title/owner)")
    return reasons


def _age_days(task: dict, now):
    created = _parse_iso(task.get("created_at"))
    if created is not None:
        return (now - created).days
    last_act = _parse_iso(task.get("last_activity"))
    if last_act is not None:
        return (now - last_act).days
    return None


@click.command(
    "list-stale",
    **spec_command_kwargs(
        summary="List PENDING cards that match the stale-review criteria.",
        description=(
            "Mirrors the board's `/stale` endpoint (PR #153) + the "
            "🧹 Stale Review panel (PR #154) so the operator can sweep "
            "from a shell. Default cutoff: 14 days."
        ),
        examples=(
            ("{prog} list-stale", ""),
            ("{prog} list-stale --days 30 --exclude-no-timestamp", ""),
            ("{prog} list-stale --project scitex-dev --json", ""),
        ),
    ),
)
@click.option(
    "--days",
    type=int,
    default=_DEFAULT_DAYS,
    show_default=True,
    help="Age cutoff in days for `created_at` / `last_activity`.",
)
@click.option(
    "--include-no-timestamp/--exclude-no-timestamp",
    "include_no_timestamp",
    default=True,
    show_default=True,
    help=(
        "Whether to include rows flagged ONLY because they have no"
        " timestamps (~70% of the default flagged set per the 2026-06-13"
        " sweep — pass --exclude-no-timestamp to focus on the truly old)."
    ),
)
@click.option(
    "--project",
    default=None,
    help="Restrict to one project (matches the `project` field exactly).",
)
@click.option(
    "--assignee",
    default=None,
    help="Restrict to one assignee (matches `assignee` field exactly).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the result as a JSON array (machine-readable).",
)
def list_stale_cmd(days, include_no_timestamp, project, assignee, as_json) -> None:
    if days < 0:
        raise click.UsageError("--days must be non-negative")

    resolved = resolve_tasks_path(None)
    tasks = load_tasks(resolved)

    now = datetime.datetime.now(datetime.timezone.utc)
    cut = now - datetime.timedelta(days=days)

    rows: list[dict] = []
    for t in tasks:
        # ``deferred`` is the backlog status since ``pending`` was abolished
        # (2026-07-10). Matching on the dead value made this command a no-op.
        if t.get("status") != BACKLOG_STATUS:
            continue
        if project is not None and (t.get("project") or "") != project:
            continue
        if (
            assignee is not None
            and (t.get("assignee") or t.get("agent") or "") != assignee
        ):
            continue
        reasons = _stale_reasons(t, now, cut)
        if not reasons:
            continue
        only_no_ts = reasons == ["no created_at + no last_activity"]
        if not include_no_timestamp and only_no_ts:
            continue
        rows.append(
            {
                "id": t["id"],
                "title": t.get("title") or "",
                "project": t.get("project") or "(none)",
                "assignee": t.get("assignee") or t.get("agent") or "",
                "priority": t.get("priority"),
                "created_at": t.get("created_at"),
                "last_activity": t.get("last_activity"),
                "age_days": _age_days(t, now),
                "reasons": reasons,
            }
        )

    rows.sort(
        key=lambda r: (0, -r["age_days"]) if r["age_days"] is not None else (1, 0)
    )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        click.echo(f"# no stale cards match the current criteria (days={days})")
        return

    click.echo(
        f"# {len(rows)} stale candidate(s) "
        f"(days={days}, include_no_timestamp={include_no_timestamp})"
    )
    for r in rows:
        age = "—" if r["age_days"] is None else f"{r['age_days']}d"
        reason_str = "; ".join(r["reasons"])
        click.echo(f"  {r['id']:55} | {r['project']:20} | {age:>6} | {reason_str}")


def register(main: click.Group) -> None:
    """Attach the `list-stale` verb (+ hidden warn-phase `stale-list` alias).

    Renamed in the slice-6b verb-rename pilot: `stale-list` was a
    noun-first compound; doctrine §1d grammar requires kebab-case
    VERB-FIRST compounds (`list-stale`, like `list-tasks`). The old
    name forwards with a once-per-shell stderr warning until v0.9.
    """
    main.add_command(list_stale_cmd, name="list-stale")
    deprecated_alias(main, "stale-list", target="list-stale", remove_in="0.9")


# EOF
