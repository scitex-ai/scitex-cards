#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards mcp install` / `install-fleet` verbs.

Extracted from ``_cli/_mcp.py`` (size cap) — the install verbs are the
file's largest cluster. ``attach_install_verbs(mcp_group)`` wires both onto
an existing ``mcp`` click group; ``_fleet_apply_one`` is the shared
single-file merge body (re-exported by ``_mcp.py`` for historical imports).
"""

from __future__ import annotations

import json
import pathlib

import click

from ._compat import spec_command_kwargs

#: The `mcpServers` KEY we write, and the `command` the entry execs. The key is
#: the namespace agents see their tools under (`mcp__scitex-cards__add_task`),
#: so it must say the current name.
_CLI_NAME = "scitex-cards"

#: The key we used to write. Kept ONLY so we can RETIRE it — see
#: :func:`_retire_legacy_entry`. This is not a fallback we ever write.
LEGACY_CLI_NAME = "scitex-cards"

#: Commands that mark an `.mcp.json` entry as OURS. Both console scripts are
#: installed (``scitex-cards`` stays a permanent alias), so a legacy entry may
#: exec either one.
_OUR_COMMANDS = frozenset({"scitex-cards", "scitex-cards"})


def _is_our_entry(entry: object) -> bool:
    """True when ``entry`` is an MCP server entry THIS package wrote.

    Deliberately strict: it must exec one of our console scripts (basename, so
    an absolute path still matches) AND run the ``mcp`` subcommand. Anything
    else that merely happens to sit under the legacy key belongs to someone
    else and must not be touched.
    """
    if not isinstance(entry, dict):
        return False
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return False
    if pathlib.PurePath(command).name not in _OUR_COMMANDS:
        return False
    args = entry.get("args")
    return isinstance(args, list) and bool(args) and args[0] == "mcp"


def _retire_legacy_entry(servers: dict) -> bool:
    """Drop OUR stale ``scitex-cards`` entry from ``servers``. Returns whether it did.

    Renaming the key we write is only half a migration. Left alone, a config
    that already had ``scitex-cards`` would end up with BOTH keys pointing at the
    same server — the agent would load two copies of every tool
    (``mcp__scitex-cards__add_task`` *and* ``mcp__scitex-cards__add_task``),
    both writing the same store. So writing the new key RETIRES the old one.

    Only OUR entry is retired (see :func:`_is_our_entry`); a third-party server
    that happens to be keyed ``scitex-cards`` is left exactly as found.
    """
    legacy = servers.get(LEGACY_CLI_NAME)
    if legacy is None or not _is_our_entry(legacy):
        return False
    del servers[LEGACY_CLI_NAME]
    return True


def attach_install_verbs(mcp_group: click.Group) -> None:
    """Attach the ``install`` + ``install-fleet`` verbs to ``mcp_group``."""

    @mcp_group.command(
        "install",
        **spec_command_kwargs(
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
                ("{prog} mcp install --format raw", "Print the raw snippet."),
                ("{prog} mcp install --apply --dry-run", "Preview a user-scope apply."),
                ("{prog} mcp install --apply -y", "Write into ~/.mcp.json."),
                (
                    "{prog} mcp install --apply --to ./.mcp.json",
                    "Project-scope .mcp.json.",
                ),
                (
                    "{prog} mcp install --apply --to to_home/.mcp.json "
                    "--env-tasks-path /home/agent/.scitex/cards/cards.db -y",
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
            "Pin $SCITEX_CARDS_DB in the snippet's `env` block — the MCP\n"
            "subprocess uses this database path as the store (the sole store\n"
            "identity). Fleet use case: when this CLI is run by agent-container\n"
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
            "command": _CLI_NAME,
            "args": ["mcp", "start"],
        }
        # Host-store wire-up: when an explicit database path is provided, pin
        # it in the MCP entry's `env` block as $SCITEX_CARDS_DB (the sole store
        # identity), so a containerized agent reads the shared host store
        # regardless of its $HOME or symlink state. OPT-IN preserves back-compat
        # with the existing snippet shape.
        if env_tasks_path:
            entry["env"] = {"SCITEX_CARDS_DB": env_tasks_path}
        snippet = {"mcpServers": {_CLI_NAME: entry}}

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
        before_entry = servers.get(_CLI_NAME)
        servers[_CLI_NAME] = snippet["mcpServers"][_CLI_NAME]
        retired = _retire_legacy_entry(servers)
        merged["mcpServers"] = servers

        # A retirement alone is a real change: the file already had the new key
        # but ALSO the stale one, and leaving it would double every tool.
        changed = before_entry != servers[_CLI_NAME] or retired
        if not changed:
            action = "noop (entry already present)"
        else:
            action = "would update" if before_entry is not None else "would create"
            if retired:
                action += f" (retiring the legacy {LEGACY_CLI_NAME} entry)"

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
            click.echo(f"# noop: {target} already has the {_CLI_NAME} entry")
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

    @mcp_group.command(
        "install-fleet",
        **spec_command_kwargs(
            summary="Apply the MCP entry to EVERY agent's to_home/.mcp.json.",
            description=(
                "Fleet P3a unblock (lead a2a 1ab212f3, 2026-06-14). Walks "
                "<agents-dir>/*/to_home/.mcp.json and runs the idempotent "
                "merge for each. Files that don't exist yet are CREATED; "
                "existing files preserve every sibling MCP server entry.",
            ),
            examples=(
                (
                    "{prog} mcp install-fleet "
                    "--agents-dir ~/.dotfiles/src/.scitex/agent-container/agents "
                    "--env-tasks-path /home/agent/.scitex/cards/cards.db -y",
                    "Sweep every agent's to_home/.mcp.json.",
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
        help="Pin $SCITEX_CARDS_DB in every emitted entry's env block.",
    )
    @click.option(
        "--dry-run", is_flag=True, help="Print planned per-agent action; no writes."
    )
    @click.option("-y", "--yes", "yes", is_flag=True, help="Skip per-agent prompts.")
    def install_fleet(agents_dir, env_tasks_path, dry_run, yes) -> None:
        """Apply the scitex-cards MCP entry to every agent's to_home/.mcp.json."""
        agents_root = pathlib.Path(agents_dir).expanduser()
        if not agents_root.is_dir():
            raise click.ClickException(
                f"agents-dir does not exist or is not a directory: {agents_root}"
            )
        entry: dict = {"command": _CLI_NAME, "args": ["mcp", "start"]}
        if env_tasks_path:
            entry["env"] = {"SCITEX_CARDS_DB": env_tasks_path}

        agent_count = applied = noop = 0
        errors: list[str] = []
        for agent_dir in sorted(agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_count += 1
            target = agent_dir / "to_home" / ".mcp.json"
            try:
                action, changed = _fleet_apply_one(target, entry, dry_run=dry_run)
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


def _fleet_apply_one(target, entry: dict, *, dry_run: bool):
    """Apply / merge the scitex-cards MCP entry into ONE target file.

    Shared body for ``install-fleet``. Same rules as the single-file
    ``install --apply``: existing JSON preserved + sibling mcpServers
    kept; idempotent re-application is a noop; dry-run prints the
    planned action without touching disk; OUR stale ``scitex-cards`` entry is
    retired so no agent ends up loading both copies. Returns
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
    before_entry = servers.get(_CLI_NAME)
    servers[_CLI_NAME] = entry
    retired = _retire_legacy_entry(servers)
    merged["mcpServers"] = servers
    changed = before_entry != entry or retired
    if not changed:
        action = "noop (entry already present)"
    else:
        action = "would-update" if before_entry is not None else "would-create"
        if retired:
            action += f" (retiring the legacy {LEGACY_CLI_NAME} entry)"
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


__all__ = [
    "attach_install_verbs",
    "_fleet_apply_one",
    "_retire_legacy_entry",
    "_is_our_entry",
    "LEGACY_CLI_NAME",
]

# EOF
