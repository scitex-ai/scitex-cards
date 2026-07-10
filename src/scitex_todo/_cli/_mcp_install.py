#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``mcp install`` / ``mcp install-fleet`` — the two config-writing verbs
of the fallback ``mcp`` group.

Extracted from ``_cli/_mcp.py`` (which had grown past the 512-line
convention) into its own module — same split shape every other
verb-group in this package already uses. ``attach(mcp_group, cli_name)``
defines both commands and adds them to the group built in ``_mcp.py``.
Pure extraction: no behavior change.
"""

from __future__ import annotations

import json
import pathlib

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand


def _fleet_apply_one(target, entry: dict, cli_name: str, *, dry_run: bool):
    """Apply / merge the MCP entry into ONE target file.

    Shared body for ``install-fleet``. Same rules as the single-file
    ``install --apply``: existing JSON preserved + sibling mcpServers
    kept; idempotent re-application is a noop; dry-run prints the
    planned action without touching disk. Returns
    ``(action_label, changed)``.
    """
    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"target {target} exists but is not valid JSON: {exc}"
            ) from None
        if not isinstance(existing, dict):
            raise RuntimeError(
                f"target {target} root is not a JSON object "
                f"(got {type(existing).__name__})"
            )
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    before_entry = servers.get(cli_name)
    servers[cli_name] = entry
    merged["mcpServers"] = servers
    changed = before_entry != entry
    action = (
        "noop (entry already present)"
        if not changed
        else ("would-update" if before_entry is not None else "would-create")
    )
    if dry_run or not changed:
        return action, changed
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            backup.write_text(
                target.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return ("updated" if before_entry is not None else "created"), True


def attach(mcp_group: click.Group, *, cli_name: str) -> None:
    """Define ``install`` / ``install-fleet`` and add them to ``mcp_group``."""

    # ── install ───────────────────────────────────────────────────────── #
    @mcp_group.command(
        "install",
        cls=SpecCommand,
        help_spec=CliHelp(
            summary="Print or apply the Claude Code MCP install snippet.",
            description=(
                "Without --apply this command PRINTS the JSON snippet "
                "for manual paste-in. With --apply, it MERGES the "
                "snippet into the target .mcp.json file (default "
                "~/.mcp.json), idempotently — re-running is a no-op "
                "when the entry is already present. Other servers' "
                "entries are preserved.",
            ),
            examples=(
                Example("{prog} mcp install --format raw", "Print the raw snippet."),
                Example("{prog} mcp install --apply --dry-run", "Preview a user-scope apply."),
                Example("{prog} mcp install --apply -y", "Write into ~/.mcp.json."),
                Example("{prog} mcp install --apply --to ./.mcp.json", "Project-scope .mcp.json."),
                Example(
                    "{prog} mcp install --apply --to to_home/.mcp.json "
                    "--env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y",
                    "Fleet host-store pin (P3a).",
                ),
            ),
        ),
    )
    @click.option(
        "--format",
        "fmt",
        type=click.Choice(["claude-code", "raw"]),
        default="claude-code",
        show_default=True,
    )
    @click.option(
        "--apply",
        "do_apply",
        is_flag=True,
        help=(
            "Write the snippet into the target ``.mcp.json`` file"
            " (default ``~/.mcp.json``). Idempotent + non-destructive."
        ),
    )
    @click.option(
        "--to",
        "target_path",
        type=click.Path(
            dir_okay=False, file_okay=True, writable=True, resolve_path=False
        ),
        default=None,
        help=(
            "Override the target file (default ``~/.mcp.json``)."
            " Only meaningful with --apply."
        ),
    )
    @click.option(
        "--env-tasks-path",
        "env_tasks_path",
        type=str,
        default=None,
        help=(
            "Pin SCITEX_TODO_TASKS in the snippet's `env` block — the MCP\n"
            "subprocess uses this path as the task-store via the normal\n"
            "resolution chain (explicit env > project > user > example).\n"
            "Fleet P3a use case: when this CLI is run by agent-container\n"
            "to seed every container's ``to_home/.mcp.json``, the pinned\n"
            "path makes the wire-up self-documenting and immune to $HOME\n"
            "or symlink drift in any container. Omit to leave the entry\n"
            "without an env block (back-compat default)."
        ),
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help=(
            "With --apply: print what would be written WITHOUT touching"
            " the file. Without --apply: print the snippet (same as default)."
        ),
    )
    @click.option(
        "-y",
        "--yes",
        is_flag=True,
        help=(
            "With --apply: skip the confirmation prompt. Without"
            " --apply: no-op (print-only path needs no confirmation)."
        ),
    )
    def install(fmt, do_apply, target_path, env_tasks_path, dry_run, yes) -> None:
        entry: dict = {
            "command": cli_name,
            "args": ["mcp", "start"],
        }
        # P3a host-store wire-up: when an explicit task-store path is
        # provided, pin it in the MCP entry's `env` block. The MCP server
        # subprocess picks up SCITEX_TODO_TASKS via the normal resolution
        # chain (env beats project/user scopes), so a containerized agent
        # ends up reading the shared host store regardless of its $HOME or
        # symlink state. Keeping this OPT-IN preserves back-compat with
        # the existing snippet shape.
        if env_tasks_path:
            entry["env"] = {"SCITEX_TODO_TASKS": env_tasks_path}
        snippet = {"mcpServers": {cli_name: entry}}

        if not do_apply:
            # Print-only path (back-compat).
            if fmt == "raw":
                click.echo(json.dumps(snippet["mcpServers"]))
                return
            click.echo(json.dumps(snippet, indent=2))
            return

        # --apply: MERGE the entry into the target file. Fleet P3a path.
        target = (
            pathlib.Path(target_path).expanduser()
            if target_path
            else pathlib.Path.home() / ".mcp.json"
        )

        existing: dict = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise click.ClickException(
                    f"target {target} exists but is not valid JSON: {exc}"
                ) from None
            if not isinstance(existing, dict):
                raise click.ClickException(
                    f"target {target} root is not a JSON object "
                    f"(got {type(existing).__name__})"
                )

        merged = dict(existing)
        servers = dict(merged.get("mcpServers") or {})
        before_entry = servers.get(cli_name)
        servers[cli_name] = snippet["mcpServers"][cli_name]
        merged["mcpServers"] = servers

        changed = before_entry != servers[cli_name]
        action = (
            "noop (entry already present)"
            if not changed
            else ("would update" if before_entry is not None else "would create")
        )

        new_text = json.dumps(merged, indent=2) + "\n"

        if dry_run:
            click.echo(f"# dry-run: --apply target={target} action={action}")
            click.echo(new_text)
            return

        if not yes and changed:
            # Refuse non-interactively rather than prompting — CLI convention:
            # a mutating verb must not block on stdin; require explicit --yes.
            raise click.ClickException(
                f"Refusing to modify {target} ({action}) without confirmation"
                " — re-run with --yes/-y to apply."
            )

        if not changed:
            click.echo(f"# noop: {target} already has the {cli_name} entry")
            return

        # Backup the existing file before write (best-effort; failure to
        # backup does NOT abort the write — but if the source was unreadable
        # we already failed above with ClickException).
        if target.exists():
            backup = target.with_suffix(target.suffix + ".bak")
            try:
                backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_text, encoding="utf-8")
        click.echo(f"# applied: {target} updated ({action})")

    # ── install-fleet ─────────────────────────────────────────────────── #
    @mcp_group.command(
        "install-fleet",
        cls=SpecCommand,
        help_spec=CliHelp(
            summary="Apply the MCP entry to EVERY agent's to_home/.mcp.json.",
            description=(
                "Fleet P3a unblock (lead a2a 1ab212f3, 2026-06-14). Walks "
                "<agents-dir>/*/to_home/.mcp.json and runs the idempotent "
                "merge for each. Files that don't exist yet are CREATED; "
                "existing files preserve every sibling MCP server entry.",
            ),
            examples=(
                Example(
                    "{prog} mcp install-fleet "
                    "--agents-dir ~/.dotfiles/src/.scitex/agent-container/agents "
                    "--env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y",
                    "Apply to every agent in the directory.",
                ),
            ),
        ),
    )
    @click.option(
        "--agents-dir",
        "agents_dir",
        type=click.Path(file_okay=False, dir_okay=True, resolve_path=False),
        required=True,
        help="Directory of per-agent subdirs each carrying `to_home/.mcp.json`.",
    )
    @click.option(
        "--env-tasks-path",
        "env_tasks_path",
        type=str,
        default=None,
        help="Pin SCITEX_TODO_TASKS in every emitted entry's env block.",
    )
    @click.option(
        "--dry-run", is_flag=True, help="Print planned per-agent action; no writes."
    )
    @click.option("-y", "--yes", "yes", is_flag=True, help="Skip per-agent prompts.")
    def install_fleet(agents_dir, env_tasks_path, dry_run, yes) -> None:
        """Apply the scitex-todo MCP entry to every agent's to_home/.mcp.json."""
        agents_root = pathlib.Path(agents_dir).expanduser()
        if not agents_root.is_dir():
            raise click.ClickException(
                f"agents-dir does not exist or is not a directory: {agents_root}"
            )
        entry: dict = {"command": cli_name, "args": ["mcp", "start"]}
        if env_tasks_path:
            entry["env"] = {"SCITEX_TODO_TASKS": env_tasks_path}

        agent_count = applied = noop = 0
        errors: list[str] = []
        for agent_dir in sorted(agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_count += 1
            target = agent_dir / "to_home" / ".mcp.json"
            try:
                action, changed = _fleet_apply_one(
                    target, entry, cli_name, dry_run=dry_run
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{agent_dir.name}: {exc}")
                click.echo(f"# error: {agent_dir.name} — {exc}", err=True)
                continue
            prefix = "# dry-run:" if dry_run else "# applied:"
            click.echo(f"{prefix} {agent_dir.name}/to_home/.mcp.json — {action}")
            if changed:
                applied += 1
            else:
                noop += 1
        click.echo(
            f"# fleet sweep: agents={agent_count} "
            f"{'would-update' if dry_run else 'updated'}={applied} "
            f"noop={noop} errors={len(errors)}",
            err=True,
        )
        if errors and not yes:
            raise click.ClickException(
                f"install-fleet completed with {len(errors)} error(s)"
            )


# EOF
