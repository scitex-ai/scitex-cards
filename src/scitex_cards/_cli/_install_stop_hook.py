#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards install-stop-hook`` — wire the Stop hook into settings.json.

WHY THIS SHIPS WITH CARDS (operator, 2026-07-18): 「フックとしてスクリプトを
用意してあげたらいいのかな？」. Registration was the last manual step between a
merged mechanism and a mechanism that actually runs — and a manual step is a
step that silently does not happen. The operator asked whether an agent with
cards left can still stop; it could, because the hook was registered nowhere.
A capability nobody wired protects nobody.

WHAT THIS IS NOT: it does not decide policy. Cards owns one verb — *does this
identity have runnable work, and what is the next action* — and this command
writes the four lines of JSON that let a runtime ask it. Anyone who prefers to
edit settings.json by hand should; this exists so nobody has to.

SAFETY, because this edits a file the user did not write:
* IDEMPOTENT — re-running never duplicates the entry.
* DRY-RUN BY DEFAULT. It prints what it would change and exits. ``--apply``
  is required to write, per the constitution's dry-run-every-mutation rule.
* BACKUP before every write, to ``<settings>.bak-<n>``, so the previous file
  survives even a bad edit.
* PRESERVES everything else. The file is read as JSON and re-serialised with
  only the hooks entry added — other hooks, permissions and env stay put.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

#: The command the hook runs. Absolute-path resolution is deliberately NOT
#: baked in: the whole point is that whichever `scitex-cards` is on the
#: runtime's PATH answers, and pinning a venv path here would rot the moment
#: the fleet upgrades. Callers who need a pin can pass --command.
_DEFAULT_COMMAND = "scitex-cards stop-hook"

_EVENT = "Stop"


def _load(path: Path) -> dict:
    """Read settings.json, or an empty doc when absent. Never invents fields."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"{path} is not readable JSON ({type(exc).__name__}: {exc}). "
            "Refusing to touch it — fix or move the file first."
        ) from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"{path} is not a JSON object; refusing to edit.")
    return data


def _already_registered(data: dict, command: str) -> bool:
    """True when SOME Stop hook already runs this command."""
    for group in data.get("hooks", {}).get(_EVENT, []) or []:
        for hook in (group or {}).get("hooks", []) or []:
            if (hook or {}).get("command", "").strip() == command.strip():
                return True
    return False


def _with_hook(data: dict, command: str) -> dict:
    """Return a copy of ``data`` with the Stop hook appended."""
    out = json.loads(json.dumps(data))  # deep copy; never mutate the caller's
    hooks = out.setdefault("hooks", {})
    groups = hooks.setdefault(_EVENT, [])
    groups.append({"hooks": [{"type": "command", "command": command}]})
    return out


def _backup(path: Path) -> Path | None:
    """Copy the current file aside. Returns the backup path, or None if absent."""
    if not path.exists():
        return None
    for n in range(1, 1000):
        candidate = path.with_suffix(path.suffix + f".bak-{n}")
        if not candidate.exists():
            candidate.write_bytes(path.read_bytes())
            return candidate
    raise click.ClickException("could not allocate a backup filename")


@click.command("install-stop-hook")
@click.option(
    "--settings",
    "settings_path",
    default=None,
    help="settings.json to edit (default: ~/.claude/settings.json).",
)
@click.option(
    "--command",
    "command",
    default=_DEFAULT_COMMAND,
    help=f"Command the hook runs (default: {_DEFAULT_COMMAND!r}).",
)
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    help="Actually write. Without this the command only reports what it would do.",
)
def install_stop_hook_cmd(settings_path, command, apply_):
    """Register `scitex-cards stop-hook` as a Claude Code Stop hook.

    Refuses a stop while the agent's board holds runnable work. Dry-run
    unless --apply is passed.

    Example:
      $ scitex-cards install-stop-hook
      $ scitex-cards install-stop-hook --apply
    """
    path = (
        Path(settings_path).expanduser()
        if settings_path
        else Path.home() / ".claude" / "settings.json"
    )
    data = _load(path)

    if _already_registered(data, command):
        click.echo(f"# already registered in {path} — nothing to do")
        click.echo(f"  command: {command}")
        return

    if not apply_:
        click.echo(f"# DRY RUN — would register the Stop hook in {path}")
        click.echo(f"  command: {command}")
        click.echo(
            f"  existing {_EVENT} groups: {len(data.get('hooks', {}).get(_EVENT, []) or [])}"
        )
        click.echo("  re-run with --apply to write (a backup is made first)")
        return

    backup = _backup(path)
    updated = _with_hook(data, command)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

    # READ IT BACK. A write that was not verified is a claim, not a result —
    # and this file decides whether the mechanism runs at all.
    if not _already_registered(_load(path), command):
        raise click.ClickException(
            f"wrote {path} but the hook is NOT present on read-back — "
            f"restore from {backup} and investigate"
        )
    click.echo(f"# registered the Stop hook in {path}")
    click.echo(f"  command: {command}")
    if backup:
        click.echo(f"  backup:  {backup}")
    click.echo("  verified by read-back")


def register(main) -> None:
    main.add_command(install_stop_hook_cmd)


__all__ = ["register", "install_stop_hook_cmd"]

# EOF
