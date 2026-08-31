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

from .._db import resolve_db_path
from .._store_target import store_label
from ._compat import spec_command_kwargs, spec_group_kwargs


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
    resolved = store_label(tasks_path)
    click.echo(f"# {resolved}  ({len(rows)} tasks)")
    for task in rows:
        sc = task.get("scope") or "-"
        click.echo(f"{task['id']:<24} {task['status']:<12} {sc:<28} {task['title']}")


def list_blocking_operator(tasks_path: str | None, as_json: bool) -> None:
    """Print the operator's decision queue — a glanceable, project-grouped view.

    Surfaces the tasks the OPERATOR is blocking (the ``blocking_me`` predicate:
    ``status=blocked AND blocker=operator-decision``) so the operator can see
    and clear the queue at a glance. Grouped by ``project`` (falling back to
    ``scope``), each row shows the title plus the first line of the card's
    ``note`` as the WHY / how-to-unblock context. A card with no note is
    flagged so the owner knows to add the decision context (the common reason a
    block is un-actionable). ``--json`` emits the raw matching rows for tooling.
    """
    from .. import _store

    rows = _store.list_tasks(tasks_path, blocking_me=True)
    if as_json:
        click.echo(json.dumps(rows))
        return
    resolved = store_label(tasks_path)
    if not rows:
        click.echo("✓ Nothing is waiting on the operator (0 operator-decision blocks).")
        click.echo(f"# {resolved}")
        return

    groups: dict[str, list[dict]] = {}
    for task in rows:
        key = task.get("project") or task.get("scope") or "(no project)"
        groups.setdefault(key, []).append(task)

    click.echo(
        f"# Waiting on operator — {len(rows)} decision(s) "
        f"across {len(groups)} project(s)"
    )
    click.echo(f"# {resolved}")
    for proj in sorted(groups):
        members = groups[proj]
        click.echo(f"\n{proj}  ({len(members)})")
        for task in members:
            click.echo(f"  • {task['id']:<28} {task['title']}")
            note = (task.get("note") or "").strip()
            if note:
                click.echo(f"      ↳ {note.splitlines()[0]}")
            else:
                click.echo(
                    "      ↳ (no context noted — ask the owner to add why + options)"
                )
    click.echo(
        "\nClear a block from the board, or via the CLI update/resolve verbs "
        "once you've decided."
    )


# --------------------------------------------------------------------------- #
# resolve-store (was `where` — renamed per audit §1: noun-like leaf)          #
# --------------------------------------------------------------------------- #
@click.command(
    "resolve-store",
    **spec_command_kwargs(
        summary="Show which store would be used and the precedence chain.",
        description=(
            "Prints the resolved store path plus every candidate in the "
            "precedence chain — the debugging tool for 'why is my task "
            "not showing up.'",
        ),
        examples=(("{prog} resolve-store", "Show the resolved store."),),
    ),
)
@click.option("--json", "as_json", is_flag=True)
def resolve_store_cmd(as_json) -> None:
    """Resolve the store path and print the chain so agents can verify."""
    from .. import _store
    from .._store_target import StoreTargetNotConfigured

    # THE VERB THE REFUSAL TELLS YOU TO RUN, so it is the one verb that must not
    # die on the case it exists to diagnose. Since 2026-08-13 an unconfigured
    # store raises instead of resolving to ~/.scitex/cards/cards.db, and
    # `refuse_zero_config_default`'s own message ends "Run `scitex-cards
    # resolve-store` to see what this process resolves." An operator following
    # that instruction and getting a traceback reads it as "the store is
    # broken", which is the 2026-07-31 failure repeated -- back then this verb
    # crashed on PostgreSQL while `list-tasks` served 2973 cards.
    #
    # HANDLED HERE AND NOT IN `_store.resolve_store` ON PURPOSE. The Python and
    # MCP surfaces SHOULD raise: an absent store raising rather than handing
    # back an empty board is their stated contract, and an agent needs the
    # exception, not a dict it might not inspect. A human at a terminal needs
    # the sentence. Same fact, two audiences.
    try:
        info = _store.resolve_store(None)
    except StoreTargetNotConfigured as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(info))
        return
    click.echo(f"resolved:        {info['resolved']}")
    click.echo(f"backend:         {info['backend']}")
    # Printed right under `backend` because `exists` is only meaningful for a
    # file store; on a server it is None, and a bare "None" with no backend
    # beside it is the kind of output an operator reads as "broken".
    click.echo(f"exists:          {info['exists']}")
    click.echo(f"explicit:        {info['explicit']}")
    click.echo(f"$SCITEX_CARDS_DB:  {info['db_env']}")
    click.echo(f"user store:      {info['user_store']}")


# --------------------------------------------------------------------------- #
# init-store (was `init` — renamed per audit §1: needs object noun)           #
# --------------------------------------------------------------------------- #
@click.command(
    "init-store",
    **spec_command_kwargs(
        summary="Create an empty SQLite task store at the chosen scope (idempotent).",
        description=(
            "--shared -> ~/.scitex/cards/cards.db (user scope, the "
            "default). --project -> <git-root>/.scitex/cards/cards.db. "
            "Creates an empty, schema-complete SQLite DB. No-op (prints "
            "'exists') when the target DB already exists.",
        ),
        examples=(("{prog} init-store --shared", "Create the user-scope store."),),
    ),
)
@click.option(
    "--shared",
    "scope_choice",
    flag_value="shared",
    default="shared",
    help="Create the user-scope SQLite store (~/.scitex/cards/cards.db).",
)
@click.option(
    "--project",
    "scope_choice",
    flag_value="project",
    help="Create <git-root>/.scitex/cards/cards.db instead.",
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
    """Create an empty, schema-complete SQLite store at the chosen scope."""
    _ = yes  # accepted for §2 compliance
    from pathlib import Path

    from .._db import connect, init_schema
    from .._paths import _find_git_root

    if scope_choice == "project":
        git_root = _find_git_root(Path.cwd())
        if git_root is None:
            raise click.ClickException(
                "`--project` requires running inside a git repo; "
                "no `.git` directory found in any parent of "
                f"{Path.cwd()}"
            )
        target = git_root / ".scitex" / "cards" / "cards.db"
    else:
        # THE SHARED SCOPE NAMES NO PATH, so it inherits whatever the store
        # resolves to -- and since 2026-08-13 that RAISES when nobody chose
        # one, instead of quietly meaning ~/.scitex/cards/cards.db. Asking the
        # guard first turns that into the remedy this verb's user needs; going
        # straight to `resolve_db_path` would hand them a traceback for the one
        # question they are already trying to answer. `--project`, one branch
        # up, states its own path and is deliberately not guarded.
        from ._store_guard import refuse_unconfigured_store

        refuse_unconfigured_store()
        target = resolve_db_path(None)

    if dry_run:
        click.echo(f"# dry-run: would create {target} (scope={scope_choice})")
        return
    if target.exists():
        click.echo(f"exists: {target}  (no-op)")
        return
    # The store is the canonical SQLite DB — no YAML. Create it empty and
    # schema-complete; an unstamped DB is adoptable, so the first write claims it.
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(target)
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()
    click.echo(f"created: {target}")


# --------------------------------------------------------------------------- #
# sync-store (was `sync` — renamed per audit §1: needs object noun)           #
# PHASE 1 STUB — Req 2 body lands in Phase 2.                                 #
# --------------------------------------------------------------------------- #
@click.command(
    "sync-store",
    **spec_command_kwargs(
        summary="Sync the user-scope store across hosts (PHASE-1 STUB).",
        description=(
            "Phase 2 body: `git -C ~/.scitex/card pull --rebase "
            "--autostash && git push` against an operator-owned remote. "
            "The Phase-1 stub prints the plan and exits 0 (--dry-run is "
            "the default mode) so docs/skills can reference the verb "
            "today; --apply is not yet implemented and errors.",
        ),
        examples=(("{prog} sync-store --dry-run", "Preview the planned sync."),),
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
    click.echo("# scitex-cards sync-store (PHASE-1 STUB)")
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
# store adopt-uuid — bind this database to an identity, ONCE, deliberately     #
# --------------------------------------------------------------------------- #
@click.group(
    "store",
    **spec_group_kwargs(
        summary="Store-identity operations on the resolved database.",
        description=(
            "Store IDENTITY is a uuid carried in the database, not a path. A "
            "path is not identity when more than one view can produce it — one "
            "bind-mounted cards.db has three names here, and the ownership "
            "guard refused the board its own database for a day because of it.",
        ),
    ),
)
def store_group() -> None:
    """Store-identity operations."""


@store_group.command(
    "adopt-uuid",
    **spec_command_kwargs(
        summary="Bind the resolved database to a store identity (mints one).",
        description=(
            "Writes ONE schema_meta row and prints the identity. It does NOT "
            "touch store_path (re-stamping that produced an EMPTY board on "
            "2026-07-28), does NOT touch any card row, and does NOT change "
            "what any resolver resolves. Idempotent: a database that already "
            "carries an identity keeps it and that value is printed. "
            "SEQUENCING MATTERS: stamp the store FIRST, declare the "
            "expectation ($SCITEX_CARDS_STORE_UUID, or a host-registry entry) "
            "SECOND. Publishing an expected uuid before the store carries one "
            "makes every read and write refuse.",
        ),
        examples=(
            ("{prog} store adopt-uuid", "Mint and bind an identity."),
            ("{prog} store adopt-uuid --json", "Same, machine-readable."),
        ),
    ),
)
@click.option(
    "--uuid",
    "identity",
    default=None,
    help=(
        "Bind this exact identity (bare lowercase 8-4-4-4-12) instead of "
        "minting one. For adopting an identity another scitex package already "
        "minted for the SAME database."
    ),
)
@click.option("--json", "as_json", is_flag=True)
def store_adopt_uuid_cmd(identity, as_json) -> None:
    """Bind the resolved database to a store identity and print it."""
    from .._store_uuid import adopt_store_uuid
    from ._store_guard import refuse_unconfigured_store

    # An identity is minted ONCE and forever, so "which database" must be a
    # choice somebody made. Since 2026-08-13 an unconfigured store raises
    # rather than naming ~/.scitex/cards/cards.db; the guard states that as the
    # remedy instead of a traceback, and it runs BEFORE the existence check
    # below so the two refusals cannot be confused for each other.
    refuse_unconfigured_store()
    db_path = resolve_db_path(None)
    if not db_path.exists():
        raise click.ClickException(
            f"no database at {db_path}. REFUSING to create one: an identity "
            f"belongs to a store that already exists, and manufacturing a "
            f"board here is how one gets replaced. Point $SCITEX_CARDS_DB at "
            f"the real database first."
        )
    try:
        value = adopt_store_uuid(db_path)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if identity is not None and value != identity:
        raise click.ClickException(
            f"{db_path} already carries identity {value!r}; refusing to "
            f"re-identify it as {identity!r}. A store's identity is minted "
            f"once — every check that agreed with the old value would become "
            f"retroactively meaningless."
        )
    if as_json:
        click.echo(json.dumps({"db": str(db_path), "store_uuid": value}))
        return
    click.echo(f"db:         {db_path}")
    click.echo(f"store_uuid: {value}")


# --------------------------------------------------------------------------- #
# list-importers — which PROCESSES hold this package, not which venvs have it #
# --------------------------------------------------------------------------- #
@click.command(
    "list-importers",
    **spec_command_kwargs(
        summary="List the processes that import this package, and their staleness.",
        description=(
            "Answers 'who must restart for a fix to take effect'. Reports the "
            "ENUMERATION SIZE so an empty result is evidence rather than a "
            "shrug, states the VANTAGE because a container cannot see host "
            "processes, and never substitutes a venv's version for what a "
            "process actually imports.",
        ),
        examples=(
            ("{prog} list-importers", "List importing processes."),
            ("{prog} list-importers --self", "What THIS interpreter imports."),
        ),
    ),
)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--self",
    "self_only",
    is_flag=True,
    help="Report what this interpreter imports — the one row always knowable.",
)
def list_importers_cmd(as_json, self_only) -> None:
    """Enumerate importing processes, or describe this interpreter."""
    from .._process_inventory import describe_self, scan

    if self_only:
        info = describe_self()
        if as_json:
            click.echo(json.dumps(info))
            return
        click.echo(f"pid:                  {info['pid']}")
        click.echo(f"resolved_import_path: {info['resolved_import_path']}")
        click.echo(f"resolved_version:     {info['resolved_version']}")
        return

    inv = scan()
    if as_json:
        click.echo(
            json.dumps(
                {
                    "vantage": inv.vantage,
                    "enumerated": inv.enumerated,
                    "rows": [
                        {**vars(r), "staleness": r.staleness} for r in inv.rows
                    ],
                }
            )
        )
        return
    # THE TWO HEADER LINES ARE NOT DECORATION. Without them a zero-row report is
    # indistinguishable from a scan that could not run, and from one run where
    # the processes are not visible -- the two false conclusions this verb was
    # built after.
    click.echo(f"vantage:    {inv.vantage}")
    click.echo(f"enumerated: {inv.enumerated} process(es) examined")
    click.echo(f"matched:    {len(inv.rows)}")
    for r in inv.rows:
        click.echo("")
        click.echo(f"  pid {r.pid}  [{r.lifetime_class}]  started {r.start_time}")
        click.echo(f"    cmdline:  {r.cmdline}")
        click.echo(f"    venv:     {r.venv_path} -> {r.venv_version}")
        click.echo(f"    imports:  {r.resolved_import_path}")
        click.echo(f"    stale:    {r.staleness}")
    if inv.vantage == "container":
        click.echo("")
        click.echo(
            "NOTE: run from inside a container — this /proc shows only this "
            "namespace. An empty result here means WRONG VANTAGE, not "
            "'nothing running'. Re-run on the host for a fleet answer."
        )


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #
def register(main: click.Group) -> None:
    """Attach the admin-side verbs (resolve-store / init-store / sync-store / store)."""
    main.add_command(resolve_store_cmd, name="resolve-store")
    main.add_command(init_store_cmd, name="init-store")
    main.add_command(sync_store_cmd, name="sync-store")
    main.add_command(store_group, name="store")
    main.add_command(list_importers_cmd, name="list-importers")


# EOF
