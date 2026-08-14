#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``scitex-cards dev`` group — this package's own maintenance verbs.

WHY A SEPARATE GROUP rather than mounting maintenance verbs at the root.
The root group is the PRODUCT surface: what someone managing cards runs.
A verb that maintains the store itself — reconciles two copies, reports
drift, runs on a schedule — is upkeep of the package, and mixing the two
makes the root help a list of everything anyone ever needed rather than a
description of what the tool does.

Operator ruling, 2026-08-10: 「定期コマンドは scitex-dev dev の下だったり、
scitex-dev ecosystem dev の下じゃない？」 — periodic verbs live under a
package's own ``dev`` group, and each package owns the ones that operate on
its own data. Reconciling two copies of the card store is the card store's
feature, so it is ``scitex-cards dev cardsync``, not a scitex-dev verb.

``get_dev_group`` is idempotent: several modules attach subgroups here, and
whichever registers first creates the group.
"""

from __future__ import annotations

import click

from ._compat import spec_group_kwargs

__all__ = ["get_dev_group"]


def get_dev_group(main: click.Group) -> click.Group:
    """Return the ``dev`` group on ``main``, creating it on first call."""
    existing = main.commands.get("dev")
    if isinstance(existing, click.Group):
        return existing

    @click.group(
        "dev",
        invoke_without_command=True,
        **spec_group_kwargs(
            summary="Maintenance verbs for the card store itself.",
            description=(
                "Upkeep rather than product surface: verbs that operate on "
                "the store as an object — reconciling two copies, reporting "
                "drift — and that typically run on a schedule rather than "
                "by hand.",
            ),
        ),
    )
    @click.pass_context
    def dev(ctx: click.Context) -> None:
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    main.add_command(dev)
    return dev


# EOF
