#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§1a shell-completion commands — self-contained, drop-in pattern.

Writes a static click-generated completion script to the per-package
drop-in directory (``~/.scitex/cards/runtime/completion/``). The
installer never touches shell startup files: it writes the script
atomically, skips the write when the content is unchanged, and prints
the installed path.
"""

from __future__ import annotations

import os

import click

from ._compat import spec_command_kwargs

#: BOTH console scripts get completion: the canonical name and the legacy
#: alias the un-cutover fleet still invokes. Each script must carry the env
#: var click derives from the INVOKED prog name at runtime, so the pair is
#: (prog, complete_var) — not one shared var.
_PROGS = (
    ("scitex-cards", "_SCITEX_CARDS_COMPLETE"),
    ("scitex-cards", "_SCITEX_CARDS_COMPLETE"),
)


def _completion_source(shell: str) -> str:
    """Return the click-generated completion script for BOTH prog names."""
    from click.shell_completion import get_completion_class

    from ._main import main  # lazy import: avoids a circular import at load

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise click.ClickException(f"unsupported shell: {shell}")
    return "\n".join(
        comp_cls(main, {}, prog, complete_var).source()
        for prog, complete_var in _PROGS
    )


def _atomic_write_text(target, content: str) -> bool:
    """Write ``content`` to ``target`` atomically; skip when unchanged.

    Returns True when the file was written, False when the existing
    content already matched (idempotent no-op).
    """
    try:
        existing = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    if existing == content:
        return False
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return True


@click.command(
    "print-shell-completion",
    **spec_command_kwargs(
        summary="Print the shell completion script to stdout (no filesystem changes).",
        examples=(
            (
                'eval "$({prog} print-shell-completion --shell bash)"',
                "Load completion into the current shell.",
            ),
        ),
    ),
)
@click.option(
    "--shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default="bash",
    show_default=True,
    help="Target shell.",
)
def print_shell_completion_cmd(shell: str) -> None:
    """Print the completion snippet for piping / eval."""
    click.echo(_completion_source(shell))


@click.command(
    "install-shell-completion",
    **spec_command_kwargs(
        summary="Install tab-completion into the per-package drop-in directory.",
        description=(
            "Writes the static completion script to "
            "~/.scitex/cards/runtime/completion/ and prints the installed "
            "path. Source that path from your shell to enable tab-completion."
        ),
        examples=(
            (
                "{prog} install-shell-completion --shell bash",
                "Write the bash drop-in script and print its path.",
            ),
        ),
    ),
)
@click.option(
    "--shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default="bash",
    show_default=True,
    help="Target shell.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the target path that would be written; change nothing.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Proceed without confirmation (this command never prompts anyway).",
)
def install_shell_completion_cmd(shell: str, dry_run: bool, yes: bool) -> None:
    """Write the completion drop-in script and print its path."""
    from .._paths import _user_root

    del yes  # accepted for §2 compliance; install never prompts.
    target_dir = _user_root() / "runtime" / "completion"
    target = target_dir / "scitex-cards"
    script = _completion_source(shell)

    if dry_run:
        click.echo(f"[dry-run] would write completion script -> {target}")
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, script)

    click.echo(str(target))
    click.echo("Source that path from your shell to enable tab-completion.")


def register(group: click.Group) -> None:
    """Attach the shell-completion commands to the root ``group``."""
    group.add_command(print_shell_completion_cmd)
    group.add_command(install_shell_completion_cmd)

# EOF
