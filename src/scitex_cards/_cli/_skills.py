#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§1a ``skills`` group — list / get / install the bundled agent skills.

Self-contained: walks the package's own ``_skills/scitex-cards/`` directory; no
scitex-dev runtime dependency.
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs, spec_group_kwargs

_SKILLS_PKG = "scitex-cards"

#: The pre-rename directory name. This is an ON-DISK PATH that other packages
#: resolve directly, so dropping it outright is a breaking change to something
#: nobody declared — it is a MIGRATION, not a rename. Kept as a read fallback
#: for the transition window: a checkout or wheel that still carries the old
#: directory keeps working, and nothing new is written under it.
_SKILLS_PKG_LEGACY = "scitex-todo"


def _skills_root():
    """Resolve the bundled ``_skills/scitex-cards/`` directory.

    Falls back to the legacy directory ONLY when the current one is absent, so
    a half-migrated tree degrades to "still works" instead of "no skills found".
    """
    from pathlib import Path

    import scitex_cards

    base = Path(scitex_cards.__file__).parent / "_skills"
    root = base / _SKILLS_PKG
    if not root.is_dir():
        legacy = base / _SKILLS_PKG_LEGACY
        if legacy.is_dir():
            return legacy
    return root


def _list_skill_files(root):
    """All ``.md`` files under the skills root (recursive), excluding SKILL.md."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file() and p.name != "SKILL.md")


@click.group(
    "skills",
    **spec_group_kwargs(
        summary="List / get / install the bundled agent skills.",
        command_categories=[
            ("Core", ["list", "get", "install", "manifest", "propagate"])
        ],
    ),
)
def skills_grp() -> None:
    """Agent-facing skills bundled with scitex-cards (`_skills/scitex-cards/`)."""


@skills_grp.command(
    "list",
    **spec_command_kwargs(
        summary="List bundled skill files.",
        examples=(("{prog} skills list --json", "Machine-readable listing."),),
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def skills_list_cmd(as_json: bool) -> None:
    """List the skill files bundled with this package."""
    root = _skills_root()
    files = _list_skill_files(root)
    if as_json:
        click.echo(json.dumps([{"name": p.stem, "path": str(p)} for p in files]))
        return
    if not files:
        click.echo(f"no skills found at {root}", err=True)
        raise SystemExit(1)
    for path in files:
        click.echo(f"{path.stem:32s}  {path.relative_to(root)}")


@skills_grp.command(
    "get",
    **spec_command_kwargs(
        summary="Print a skill file by NAME.",
        examples=(("{prog} skills get 02_quick-start", "Print one skill file."),),
    ),
)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def skills_get_cmd(name: str, as_json: bool) -> None:
    """Print the contents of a bundled skill file by NAME (e.g. ``02_quick-start``)."""
    root = _skills_root()
    target_stem = name[:-3] if name.endswith(".md") else name
    match = next((p for p in _list_skill_files(root) if p.stem == target_stem), None)
    if match is None:
        click.echo(f"skill not found: {name}", err=True)
        available = ", ".join(p.stem for p in _list_skill_files(root)[:8])
        click.echo(f"available: {available}", err=True)
        raise SystemExit(1)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "name": match.stem,
                    "path": str(match),
                    "content": match.read_text(encoding="utf-8"),
                }
            )
        )
        return
    click.echo(match.read_text(encoding="utf-8"))


@skills_grp.command(
    "install",
    **spec_command_kwargs(
        summary="Symlink the bundled skills into ~/.scitex/dev/skills/scitex-cards/.",
        description=(
            "Symlinks by default (--no-link copies instead). "
            "--claude-symlink also exposes the install at "
            "~/.claude/skills/scitex/ for Claude Code consumers.",
        ),
        examples=(
            (
                "{prog} skills install --claude-symlink",
                "Install + link for Claude Code.",
            ),
        ),
    ),
)
@click.option(
    "--dest",
    type=click.Path(),
    default=None,
    help="Destination dir (default: ~/.scitex/dev/skills/).",
)
@click.option(
    "--no-link",
    "no_link",
    is_flag=True,
    help="Copy files instead of symlinking (default: symlink).",
)
@click.option(
    "--claude-symlink",
    is_flag=True,
    help="Also expose at ~/.claude/skills/scitex/ for Claude Code consumers.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview the link/copy target; change nothing.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Proceed without confirmation (install is non-interactive).",
)
def skills_install_cmd(
    dest: str | None,
    no_link: bool,
    claude_symlink: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    """Install this package's skills into a target directory (symlink by default)."""
    import os
    import shutil
    from pathlib import Path

    del yes  # accepted for §2 compliance; install never prompts.
    src = _skills_root().resolve()
    if not src.is_dir():
        click.echo(f"no skills directory at {src}", err=True)
        raise SystemExit(1)

    base = (
        Path(dest).expanduser() if dest else Path.home() / ".scitex" / "dev" / "skills"
    )
    target = base / _SKILLS_PKG

    if dry_run:
        action = "copy" if no_link else "symlink"
        click.echo(f"[dry-run] would {action} {src} -> {target}")
        if claude_symlink:
            link = Path.home() / ".claude" / "skills" / "scitex"
            click.echo(f"[dry-run] would symlink {link} -> {base}")
        return

    base.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)

    if no_link:
        shutil.copytree(src, target)
        click.echo(f"copied {src} -> {target}")
    else:
        os.symlink(src, target, target_is_directory=True)
        click.echo(f"linked {target} -> {src}")

    if claude_symlink:
        link = Path.home() / ".claude" / "skills" / "scitex"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        if not link.exists():
            os.symlink(base.resolve(), link, target_is_directory=True)
            click.echo(f"linked {link} -> {base}")
        else:
            click.echo(
                f"warning: {link} exists and is not a symlink — skipping", err=True
            )


def register(group: click.Group) -> None:
    """Attach the ``skills`` group to the root ``group``.

    The ``manifest`` + ``propagate`` subcommands live in
    :mod:`scitex_cards._cli._skills_propagate` (kept separate so this module
    stays small + focused on the bundled-skills surface).
    """
    from ._skills_propagate import build_manifest_cmd, build_propagate_cmd

    skills_grp.add_command(build_manifest_cmd())
    skills_grp.add_command(build_propagate_cmd())
    group.add_command(skills_grp)


# EOF
