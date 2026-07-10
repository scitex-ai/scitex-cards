#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin-side CLI verbs: list-tasks-filter helper, resolve-store, init-store, sync-store.

Sibling of `_cli/_write.py` (mutation verbs). Split off to keep each
module under the 512-line file-size threshold and to group the
admin / introspection verbs (`resolve-store`, `init-store`,
`sync-store`) together — they don't share the `add` / `update` / `done`
mutation logic but they DO share the store-resolution + dry-run
conventions.

Verb names follow audit §1 (bare transitive verbs at the top level
need a noun object): `init` → `init-store`, `sync` → `sync-store`,
`where` → `resolve-store`. `resolve-store` is a deliberate §1f
exception (see `.scitex/dev/cli-audit-dict.yaml`): it means "figure
out which config file path wins," not "close a task" — the blanket
resolve→done verb-synonym mapping over-fires on this noun-scoped
usage of "resolve."
"""

from __future__ import annotations

import json

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand

from .._paths import resolve_tasks_path
from . import _write as _write_mod  # for the shared _TASKS_OPTION constant


_TASKS_OPTION = _write_mod._TASKS_OPTION


# --------------------------------------------------------------------------- #
# list-tasks filter helper (used by `list-tasks` in _cli/_main.py)            #
# --------------------------------------------------------------------------- #
def list_tasks_filtered(
    scope: str | None,
    assignee: str | None,
    status: str | None,
    as_json: bool,
    tasks_path: str | None,
    *,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> None:
    """Filter the store and print the matching tasks.

    Helper used by the merged `list-tasks` Click command in
    `_cli/_main.py` so the filter logic stays alongside the other
    `_store`-backed verbs. The `list` Click verb that used to live
    here was removed per audit §1 (bare transitive verb at top level).

    PR #66 added the new filter kwargs (agent / project / host / blocker
    / kind / id_prefix / blocking_me + multi-status via ``statuses``)
    per ADR-0008 D2 / D10. Legacy callers passing only the original four
    positional/keyword args still work; new args default to "no filter".
    """
    from .. import _store

    rows = _store.list_tasks(
        tasks_path,
        scope=scope,
        assignee=assignee,
        status=status,
        statuses=statuses,
        agent=agent,
        project=project,
        host=host,
        blocker=blocker,
        kind=kind,
        id_prefix=id_prefix,
        blocking_me=blocking_me,
        overdue=overdue,
    )
    if as_json:
        click.echo(json.dumps(rows))
        return
    resolved = resolve_tasks_path(tasks_path)
    click.echo(f"# {resolved}  ({len(rows)} tasks)")
    for task in rows:
        sc = task.get("scope") or "-"
        click.echo(
            f"{task['id']:<24} {task['status']:<12} "
            f"{sc:<28} {task['title']}"
        )


# --------------------------------------------------------------------------- #
# resolve-store (was `where` — renamed per audit §1: noun-like leaf)          #
# --------------------------------------------------------------------------- #
@click.command(
    "resolve-store",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Show which store would be used and the precedence chain.",
        description=(
            "Prints the resolved tasks.yaml path plus every candidate "
            "in the precedence chain (explicit --tasks, "
            "$SCITEX_TODO_TASKS, project store, user store, bundled "
            "example) — the debugging tool for 'why is my task not "
            "showing up.'",
        ),
        examples=(Example("{prog} resolve-store", "Show the resolved store."),),
    ),
)
@click.option("--json", "as_json", is_flag=True)
@_TASKS_OPTION
def resolve_store_cmd(as_json, tasks_path) -> None:
    """Resolve the store path and print the chain so agents can verify."""
    from .. import _store

    info = _store.resolve_store(tasks_path)
    if as_json:
        click.echo(json.dumps(info))
        return
    click.echo(f"resolved:        {info['resolved']}")
    click.echo(f"exists:          {info['exists']}")
    click.echo(f"explicit:        {info['explicit']}")
    click.echo(f"$SCITEX_TODO_TASKS: {info['env_tasks']}")
    click.echo(f"user store:      {info['user_store']}")
    click.echo(f"bundled example: {info['bundled_example']}")


# --------------------------------------------------------------------------- #
# init-store (was `init` — renamed per audit §1: needs object noun)           #
# --------------------------------------------------------------------------- #
@click.command(
    "init-store",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Create an empty task store at the chosen scope (idempotent).",
        description=(
            "--shared -> ~/.scitex/todo/tasks.yaml (user scope, the "
            "default). --project -> <git-root>/.scitex/todo/tasks.yaml. "
            "No-op (prints 'exists') when the target already exists.",
        ),
        examples=(Example("{prog} init-store --shared", "Create the user-scope store."),),
    ),
)
@click.option(
    "--shared",
    "scope_choice",
    flag_value="shared",
    default="shared",
    help="Create the user-scope store (~/.scitex/todo/tasks.yaml).",
)
@click.option(
    "--project",
    "scope_choice",
    flag_value="project",
    help="Create <git-root>/.scitex/todo/tasks.yaml instead.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the target path and exit 0 without creating it.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — init-store is non-interactive; reserved for §2).",
)
def init_store_cmd(scope_choice, dry_run, yes) -> None:
    """Materialize an empty `tasks: []` store at the chosen scope."""
    _ = yes  # accepted for §2 compliance
    from pathlib import Path

    from .._model import save_tasks
    from .._paths import _find_git_root, _user_root

    if scope_choice == "project":
        git_root = _find_git_root(Path.cwd())
        if git_root is None:
            raise click.ClickException(
                "`--project` requires running inside a git repo; "
                "no `.git` directory found in any parent of "
                f"{Path.cwd()}"
            )
        target = git_root / ".scitex" / "todo" / "tasks.yaml"
    else:
        target = _user_root() / "tasks.yaml"

    if dry_run:
        click.echo(f"# dry-run: would create {target} (scope={scope_choice})")
        return
    if target.exists():
        click.echo(f"exists: {target}  (no-op)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    save_tasks([], target)
    click.echo(f"created: {target}")


# --------------------------------------------------------------------------- #
# sync-store (was `sync` — renamed per audit §1: needs object noun)           #
# PHASE 1 STUB — Req 2 body lands in Phase 2.                                 #
# --------------------------------------------------------------------------- #
@click.command(
    "sync-store",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Sync the user-scope store across hosts (PHASE-1 STUB).",
        description=(
            "Phase 2 body: `git -C ~/.scitex/todo pull --rebase "
            "--autostash && git push` against an operator-owned remote. "
            "The Phase-1 stub prints the plan and exits 0 (--dry-run is "
            "the default mode) so docs/skills can reference the verb "
            "today; --apply is not yet implemented and errors.",
        ),
        examples=(Example("{prog} sync-store --dry-run", "Preview the planned sync."),),
    ),
)
@click.option(
    "--apply",
    "mode",
    flag_value="apply",
    help="Execute the sync (NOT IMPLEMENTED in Phase 1; will exit non-zero).",
)
@click.option(
    "--dry-run",
    "mode",
    flag_value="dry_run",
    default="dry_run",
    help="Print what would happen and exit 0 (the Phase-1 default).",
)
@click.option(
    "--remote",
    default=None,
    help="Optional remote name override; Phase 2 default = 'origin'.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — sync-store is non-interactive; reserved for §2).",
)
def sync_store_cmd(mode, remote, yes) -> None:
    """Sync stub. Prints the planned operations; --apply errors in Phase 1."""
    _ = yes  # accepted for §2 compliance
    from .._paths import _user_root

    root = _user_root()
    remote = remote or "origin"
    plan = [
        f"git -C {root} pull --rebase --autostash {remote}",
        f"git -C {root} push {remote}",
    ]
    click.echo("# scitex-todo sync-store (PHASE-1 STUB)")
    click.echo(f"# store dir: {root}")
    click.echo(f"# remote:    {remote}")
    click.echo("# planned commands:")
    for cmd in plan:
        click.echo(f"  {cmd}")
    if mode == "apply":
        raise click.ClickException(
            "--apply is not implemented in Phase 1; the git substrate "
            "lands in Phase 2 (see GITIGNORED/ARCHITECTURE.md Req 2)."
        )


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #
def register(main: click.Group) -> None:
    """Attach the admin-side verbs (resolve-store / init-store / sync-store)."""
    main.add_command(resolve_store_cmd, name="resolve-store")
    main.add_command(init_store_cmd, name="init-store")
    main.add_command(sync_store_cmd, name="sync-store")


# EOF
