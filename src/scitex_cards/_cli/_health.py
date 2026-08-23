#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-cards health`` — the package-level health doctor.

A BROAD store / identity / delivery health check (distinct from
``scitex-cards mcp doctor``, which only checks the fastmcp install). Thin
wrapper over :func:`scitex_cards._health.health`: it prints a human-readable
report by default, or the raw standard-shape JSON with ``--json``.

Exit code mirrors health: ``0`` when every check is ok, ``1`` otherwise — so
``scitex-cards health`` is usable as a shell gate / CI probe.
"""

from __future__ import annotations

import json

import click

from ._compat import deprecated_alias


def register(main: click.Group) -> None:
    """Attach ``validate-health`` to the root group, with ``health`` forwarding.

    RENAMED, NOT DICTIONARY-PATCHED. ``health`` alone failed audit §1 ("leaf
    token looks like a noun — transitive action implied") and MCP §2
    ("single-token tool names are forbidden"). The rule offers an escape
    hatch — declare the word an intransitive verb in
    ``.scitex/dev/cli-audit-dict.yaml`` — and taking it here would have been a
    lie: `scitex-cards health` does not mean "to health", it means RUN THE
    HEALTH DOCTOR. The constitution's own test settles it — if you must
    explain a name by restating it as something else, that something else IS
    the name — so the verb goes in the name.

    THE OLD NAME KEEPS WORKING BECAUSE IT IS A PUBLISHED CONTRACT. Agents,
    shell gates and CI probes call `scitex-cards health` today; a rename that
    breaks them to quiet a linter trades a working capability for a quieter
    report. Phase W: the alias forwards, warns once per shell, and carries the
    metadata the static auditor reads.
    """
    main.add_command(health_cmd)
    # remove_in is deliberately NEAR (current version is 0.48.0), not the 0.9
    # this repo's older aliases carry — those were written when the version
    # was below 0.9 and are now long overdue, which is what an alias with a
    # target nobody revisits turns into.
    deprecated_alias(main, "health", target="validate-health", remove_in="0.52")


@click.command(
    "validate-health",
    help=(
        "Run the scitex-cards health doctor: store / agent-id / notifyd / "
        "channel checks.\n\n"
        "Broader than `mcp doctor` (which only checks the fastmcp install): "
        "verifies the resolved task store is canonical + readable/writable, "
        "the agent id resolves, the notifyd delivery daemon is alive AND is "
        "actually delivering (last successful delivery + consecutive failing "
        "ticks), this agent's channel inbox is draining, and the channel server "
        "is present. Exit 0 when all checks pass, else 1.\n\n"
        "Examples:\n"
        "  scitex-cards validate-health\n"
        "  scitex-cards validate-health --json"
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the raw standard-shape JSON report.",
)
def health_cmd(as_json: bool) -> None:
    """Print the health report (human or JSON) and exit non-zero if unhealthy."""
    from .._health import health

    report = health(store=None)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["ok"] else 1)

    status = "OK" if report["ok"] else "UNHEALTHY"
    click.echo(f"# scitex-cards health: {status} — {report['summary']}")
    for check in report["checks"]:
        # THREE-VALUED. `ok is None` means the check could not measure, which is
        # neither a pass nor a fault; printing it as FAIL invents an alarm and
        # printing it as ok hides a blind spot. It gets its own mark.
        mark = {True: "ok  ", False: "FAIL"}.get(check["ok"], "????")
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
        if check["hint"]:
            click.echo(f"        hint: {check['hint']}")
    raise SystemExit(0 if report["ok"] else 1)


# EOF
