#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded imports of scitex-dev's CLI-standardization helpers (+ fallbacks).

Slice 6b of the CLI-standardization plan (pilot verb-rename migration).
scitex-dev *develop* ships ``click_compat.deprecated_alias`` and the
``help_spec`` family (``CliHelp`` / ``Example`` / ``SpecCommand`` /
``SpecGroup``) via the ``scitex_dev.ecosystem`` facade, but the latest
*released* scitex-dev (0.21.0) exposes only ``CategorizedGroup``. Per the
scitex-python#352 precedent this module guards the imports:

* **New scitex-dev installed** — re-export the real helpers (single
  source of truth; nothing here shadows them).
* **Old scitex-dev installed** — doctrine-contract fallbacks keep the
  CLI surface identical:

  - :func:`deprecated_alias` — inline Phase-W implementation. Doctrine
    §5 (``11_deprecation.md``) explicitly sanctions implementing the
    contract inline until the shared helper ships. The fleet MUST get
    warn+forward behavior for renamed verbs on every installed release,
    so this is a real implementation, not a stub: hidden alias,
    raw-passthrough re-parse, once-per-shell stderr warning keyed by the
    parent shell PID, and ``cmd._deprecated_alias`` audit metadata.
  - :func:`spec_command_kwargs` / :func:`spec_group_kwargs` — build the
    ``click.command`` / ``click.group`` keyword dict from ONE spec
    shape. With the helpers present they return
    ``{"cls": SpecCommand/SpecGroup, "help_spec": CliHelp(...)}``;
    without them they degrade to plain rendered help text (and a
    ``CategorizedGroup`` subclass for the root categories, which IS in
    the released scitex-dev).

Call sites therefore never branch on availability themselves.
"""

from __future__ import annotations

import getpass
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path

import click

try:  # scitex-dev develop (slices 2+3 merged; not in the 0.21.0 release)
    from scitex_dev.ecosystem import (
        CliHelp,
        Example,
        SpecCommand,
        SpecGroup,
    )
    from scitex_dev.ecosystem import deprecated_alias as _sd_deprecated_alias

    HAS_SPEC_HELP = True
except ImportError:  # released scitex-dev without the slice-2/3 helpers
    CliHelp = Example = SpecCommand = SpecGroup = None  # type: ignore[assignment]
    _sd_deprecated_alias = None
    HAS_SPEC_HELP = False

try:  # present in released scitex-dev >= 0.21 (categorized root help)
    from scitex_dev.ecosystem import make_categorized_group

    HAS_CATEGORIZED_GROUP = True
except ImportError:
    make_categorized_group = None  # type: ignore[assignment]
    HAS_CATEGORIZED_GROUP = False

__all__ = [
    "HAS_SPEC_HELP",
    "deprecated_alias",
    "spec_command_kwargs",
    "spec_group_kwargs",
]


# --------------------------------------------------------------------------- #
# deprecated_alias — real helper when available, doctrine-§5 inline otherwise #
# --------------------------------------------------------------------------- #
def _marker_path(old_name: str) -> Path:
    """Once-per-shell-session marker (doctrine §5/§5a — PPID-keyed)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = os.environ.get("USER", "unknown")
    return Path(base) / f"scitex-cli-dep-{user}-{os.getppid()}-{old_name}.flag"


def _warn_once(old_name: str, message: str) -> None:
    """Emit ``message`` to stderr unless this shell session already saw it."""
    marker = _marker_path(old_name)
    if marker.exists():
        return
    click.echo(message, err=True)
    try:
        marker.touch()
    except OSError:
        # An unwritable marker dir must never break the forwarded command;
        # the consequence is a warning per invocation instead of per shell.
        pass


def _fallback_deprecated_alias(
    group: click.Group,
    old_name: str,
    *,
    target: click.Command | str,
    remove_in: str,
    phase: str = "warn",
    target_name: str | None = None,
) -> click.Command:
    """Inline Phase-W alias per doctrine §5 (11_deprecation.md).

    Mirrors ``scitex_dev._ecosystem.click_compat.deprecated_alias`` for
    the warn phase — the only phase this pilot uses. Phase E/R never run
    through the fallback (fail loud rather than diverge silently).
    """
    if phase != "warn":
        raise ValueError(
            f"fallback deprecated_alias implements only phase='warn' "
            f"(got {phase!r}); upgrade scitex-dev for the full ladder"
        )
    display = target_name or (
        target.name if isinstance(target, click.Command) else target
    )
    # A GROUP alias must hand `--help` to the target, or `db --help` would
    # print the alias's own (empty) help instead of the group's verb list.
    targets_a_group = isinstance(target, click.Group)
    version = f"v{str(remove_in).lstrip('vV')}"

    @click.pass_context
    def _forward(ctx: click.Context) -> None:
        _warn_once(
            old_name,
            f"'{old_name}' is deprecated — use '{display}' (removed in {version})",
        )
        target_cmd = (
            target
            if isinstance(target, click.Command)
            else group.get_command(ctx, target)
        )
        if target_cmd is None:  # wiring bug — fail loud (exit 2)
            ctx.fail(
                f"deprecated alias misconfigured: target command "
                f"{display!r} is not registered"
            )
        # Re-parse the raw argv through the target so its own
        # options/arguments apply.
        sub_ctx = target_cmd.make_context(display, list(ctx.args), parent=ctx.parent)
        with sub_ctx:
            target_cmd.invoke(sub_ctx)

    cmd = click.Command(
        old_name,
        callback=_forward,
        params=[],
        hidden=True,
        add_help_option=not targets_a_group,
        short_help=f"(deprecated) Use '{display}'.",
        help=f"(deprecated) Forwards to '{display}'. Removed in {version}.",
        context_settings={
            "ignore_unknown_options": True,
            "allow_extra_args": True,
        },
    )
    # Static-audit metadata (slice-4 auditor contract).
    cmd._deprecated_alias = {
        "target": display,
        "remove_in": remove_in,
        "phase": phase,
    }
    group.add_command(cmd, old_name)
    return cmd


def deprecated_alias(
    group: click.Group,
    old_name: str,
    *,
    target: click.Command | str,
    remove_in: str,
    phase: str = "warn",
    target_name: str | None = None,
) -> click.Command:
    """Register ``old_name`` as a warn-phase alias of ``target`` on ``group``."""
    if _sd_deprecated_alias is not None:
        return _sd_deprecated_alias(
            group,
            old_name,
            target=target,
            remove_in=remove_in,
            phase=phase,
            target_name=target_name,
        )
    return _fallback_deprecated_alias(
        group,
        old_name,
        target=target,
        remove_in=remove_in,
        phase=phase,
        target_name=target_name,
    )


# --------------------------------------------------------------------------- #
# Spec-built help — real CliHelp/SpecCommand/SpecGroup, or plain-text render  #
# --------------------------------------------------------------------------- #
def _render_fallback_help(
    summary: str,
    description: tuple[str, ...],
    examples: tuple[tuple[str, str], ...],
    config_resolution: tuple[str, ...],
    version_of: str | None,
    # Renders every `{prog}` placeholder in every example this fallback path
    # emits, so it is the single biggest thing the CLI calls itself. The
    # scitex-dev spec-help path already says `scitex-cards` (see `_main.py`'s
    # `version_of` / `prog_name`); this default is what the NO-scitex-dev
    # install shows, and it must not disagree with it.
    prog: str = "scitex-cards",
) -> str:
    """Plain help body matching the doctrine §4 section order (fallback only)."""
    if version_of:
        try:
            first = f"{version_of} (v{_dist_version(version_of)}) — {summary}"
        except PackageNotFoundError:
            # Source-tree run without an installed dist (e.g. PYTHONPATH
            # tests): the summary still renders; only the version is absent.
            first = f"{version_of} — {summary}"
    else:
        first = summary
    blocks: list[str] = [first, *description]
    # "\b" marks the block as pre-formatted so click keeps the line breaks.
    if examples:
        lines = ["\b", "Examples:"]
        lines.extend(
            f"  $ {cmd.replace('{prog}', prog)}" + (f"  {note}" if note else "")
            for cmd, note in examples
        )
        blocks.append("\n".join(lines))
    if config_resolution:
        blocks.append("\n".join(["\b", "Config resolution:", *config_resolution]))
    return "\n\n".join(blocks)


def spec_command_kwargs(
    *,
    summary: str,
    description: str | tuple[str, ...] = (),
    examples: tuple[tuple[str, str], ...] = (),
) -> dict:
    """``click.command`` kwargs for a spec-built leaf.

    ``examples`` is a tuple of ``(cmd, note)`` pairs; ``cmd`` uses the
    ``{prog}`` placeholder per the CliHelp contract.
    """
    if isinstance(description, str):
        description = (description,)
    if HAS_SPEC_HELP:
        return {
            "cls": SpecCommand,
            "help_spec": CliHelp(
                summary=summary,
                description=description,
                examples=tuple(Example(cmd, note) for cmd, note in examples),
            ),
        }
    return {
        "help": _render_fallback_help(
            summary, tuple(description), tuple(examples), (), None
        ),
        "short_help": summary,
    }


def spec_group_kwargs(
    *,
    summary: str,
    description: str | tuple[str, ...] = (),
    config_resolution: tuple[str, ...] = (),
    version_of: str | None = None,
    command_categories: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> dict:
    """``click.group`` kwargs for the spec-built categorized root group."""
    if isinstance(description, str):
        description = (description,)
    if HAS_SPEC_HELP:
        return {
            "cls": SpecGroup,
            "help_spec": CliHelp(
                summary=summary,
                description=description,
                config_resolution=config_resolution,
                version_of=version_of,
            ),
            "command_categories": command_categories,
        }
    kwargs: dict = {
        "help": _render_fallback_help(
            summary, tuple(description), (), tuple(config_resolution), version_of
        ),
        "short_help": summary,
    }
    if HAS_CATEGORIZED_GROUP:
        kwargs["cls"] = make_categorized_group(command_categories)
    return kwargs


# EOF
