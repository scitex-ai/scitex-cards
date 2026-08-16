#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§1a shell-completion commands — self-contained, cache-file pattern.

Writes a static click-generated completion script and sources it from the
shell rc (rather than the eval-the-binary form, which re-invokes Python on
every shell start).
"""

from __future__ import annotations

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
_RC_MARKER = "# scitex-cards-completion: scitex-cards"


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
        summary="Install tab-completion by writing the script + sourcing it from your rc.",
        description=(
            "Writes the static completion script to "
            "~/.scitex/todo/runtime/completion/ and adds an idempotent "
            "source line to your shell rc file.",
        ),
        examples=(
            ("{prog} install-shell-completion --shell bash", "Install for bash."),
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
    help="Print the target path and rc line that would be written; change nothing.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Proceed without confirmation (this command never prompts anyway).",
)
def install_shell_completion_cmd(shell: str, dry_run: bool, yes: bool) -> None:
    """Cache the completion script and add an idempotent source line to the rc."""
    from pathlib import Path

    from .._paths import _user_root

    del yes  # accepted for §2 compliance; install never prompts.
    target_dir = _user_root() / "runtime" / "completion"
    target = target_dir / "scitex-cards"
    rc = {
        "bash": Path.home() / ".bashrc",
        "zsh": Path.home() / ".zshrc",
        "fish": Path.home() / ".config" / "fish" / "config.fish",
    }[shell]
    source_line = f"[ -f {target} ] && source {target}  {_RC_MARKER}"

    if dry_run:
        click.echo(f"[dry-run] would write completion script -> {target}")
        click.echo(f"[dry-run] would add to {rc}: {source_line}")
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_completion_source(shell), encoding="utf-8")

    existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    if _RC_MARKER not in existing:
        rc.parent.mkdir(parents=True, exist_ok=True)
        with rc.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{source_line}\n")

    click.echo(f"Installed {shell} completion -> {target}")
    click.echo("Open a new shell (or `source` your rc) to pick it up.")


def register(group: click.Group) -> None:
    """Attach the shell-completion commands to the root ``group``."""
    group.add_command(print_shell_completion_cmd)
    group.add_command(install_shell_completion_cmd)

# EOF
