#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI `emit-event` + `resolve-card` verbs: the no-import producer seam.

Fleet PRODUCERS (scitex-dev's C7 ``released`` / C8 ``pulled`` steps, and
future federated producers) must emit canonical card-events WITHOUT
importing :mod:`scitex_cards` — separation of concerns: a producer shells
out to ``scitex-todo`` rather than coupling to its Python API. These two
verbs are that shell-out seam:

  * ``emit-event`` constructs a canonical :class:`scitex_cards._events.Event`
    from flags and routes it through :func:`scitex_cards._events.emit` (the
    bus → C4 notify consumer → standalone pull-inbox). ``--card-id`` is
    OPTIONAL: a repo-level event like ``pulled`` emits with ``--repo`` and
    NO card, which the C4 consumer treats as a quiet no-op (nothing to
    resolve recipients against) — intended, not an error.
  * ``resolve-card`` prints the card id(s) whose ``repo`` field matches a
    given ``<R>`` (one per line; empty output when none). A producer calls
    this FIRST to map repo→card, then passes ``--card-id`` to ``emit-event``
    when a card exists, falling back to ``--repo``-only when none.

Both match the surface shape of the sibling verbs in ``_write.py`` /
``_reassign.py`` (``_emit`` JSON/human helper). They live in their own
module per the one-verb-per-file precedent (``_comment.py`` /
``_reassign.py``) — ``_write.py`` is already at its line budget.
"""

from __future__ import annotations

import json

import click

from .. import _events, _store
from ._compat import deprecated_alias, spec_command_kwargs


def _parse_extra(pairs: tuple[str, ...]) -> dict[str, str]:
    """Parse repeatable ``--extra k=v`` flags into a dict (fail-loud on shape).

    ``click``'s ``multiple=True`` yields a tuple of raw ``"k=v"`` strings.
    Each MUST contain ``=`` with a non-empty key; otherwise we raise a
    :class:`click.ClickException` (fail-loud per the SciTeX constitution —
    a producer passing a malformed ``--extra`` should hear about it, not
    have the pair silently dropped). The value may be empty and may itself
    contain ``=`` (only the FIRST ``=`` splits).
    """
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise click.ClickException(
                f"--extra must be KEY=VALUE (got {raw!r}; missing '=')"
            )
        key, value = raw.split("=", 1)
        if not key:
            raise click.ClickException(
                f"--extra must have a non-empty KEY (got {raw!r})"
            )
        out[key] = value
    return out


@click.command(
    "emit-event",
    help=(
        "Emit a canonical card-event onto the hook bus (no-import producer "
        "seam).\n\n"
        "Constructs a canonical Event from the flags and routes it through "
        "scitex_cards._events.emit (bus -> C4 notify consumer -> standalone "
        "pull-inbox). --type MUST be one of the closed EVENT_TYPES set "
        "(fails loud with the valid set otherwise). --card-id is OPTIONAL: "
        "a repo-level event (e.g. `pulled`) emits with --repo and no card, "
        "which the C4 consumer treats as a quiet no-op. Prints the dispatch "
        "summary as JSON (incl. any notify.enqueued / notify.delivered).\n\n"
        "Examples:\n"
        "  scitex-todo emit-event --type pulled --repo owner/repo\n"
        "  scitex-todo emit-event --type released --card-id my-card "
        "--repo owner/repo --version v1.2.3 --actor ci"
    ),
)
@click.option(
    "--type",
    "event_type",
    required=True,
    help="Canonical event type (closed enum — one of EVENT_TYPES).",
)
@click.option(
    "--card-id",
    "card_id",
    default=None,
    help="Board card the event concerns (optional).",
)
@click.option("--repo", default=None, help="owner/repo for git-flavoured events.")
@click.option("--branch", default=None, help="Branch name (push / commit events).")
@click.option(
    "--pr-url", "pr_url", default=None, help="Pull-request URL (merge / done events)."
)
@click.option("--sha", default=None, help="Commit sha (commit / push events).")
@click.option("--version", default=None, help="Release version / tag (release events).")
@click.option(
    "--actor",
    default=None,
    help="Who caused the event (agent id / operator / merger). Never self-notified.",
)
@click.option(
    "--extra",
    "extra",
    multiple=True,
    help="Free-form KEY=VALUE payload (repeatable).",
)
def emit_event_cmd(
    event_type,
    card_id,
    repo,
    branch,
    pr_url,
    sha,
    version,
    actor,
    extra,
) -> None:
    """Construct + emit a canonical card-event; print the dispatch summary."""
    # Fail-loud on an out-of-set type BEFORE constructing the Event, so the
    # error names the valid set (Event.__post_init__ also guards, but a CLI
    # ClickException gives the producer a clean non-zero exit + message).
    if event_type not in _events.EVENT_TYPES:
        raise click.ClickException(
            f"unknown event type {event_type!r}; must be one of "
            f"{sorted(_events.EVENT_TYPES)}"
        )
    extra_dict = _parse_extra(extra)
    event = _events.Event(
        type=event_type,
        card_id=card_id,
        actor=actor,
        repo=repo,
        branch=branch,
        pr_url=pr_url,
        sha=sha,
        version=version,
        extra=extra_dict,
    )
    # `None` means the normal precedence chain, so the C4 consumer + the
    # standalone inbox resolve the SAME ambient store this producer does.
    # emit() returns the dispatch summary (or None only if the dispatch
    # itself raised — fail-soft).
    summary = _events.emit(event, store=None)
    # Print the dispatch summary as JSON so the producer can inspect
    # notify.enqueued / notify.delivered.
    click.echo(json.dumps(summary, default=str))


@click.command(
    "find-card",
    **spec_command_kwargs(
        summary="Print the card id(s) whose `repo` matches <R> (one per line).",
        description=(
            "The producer-side repo->card lookup: a producer calls this FIRST "
            "to find a card for its repo, then passes --card-id to emit-event "
            "when one exists, falling back to --repo-only when none. Empty "
            "output when no card matches. Optionally filtered by --kind / "
            "--status (closed enums on the card)."
        ),
        examples=(
            ("{prog} find-card --repo owner/repo", ""),
            ("{prog} find-card --repo owner/repo --status in_progress", ""),
        ),
    ),
)
@click.option(
    "--repo", required=True, help="owner/repo to match the card's `repo` field."
)
@click.option("--kind", default=None, help="Optional card-kind filter (closed enum).")
@click.option(
    "--status", default=None, help="Optional card-status filter (closed enum)."
)
def find_card_cmd(repo, kind, status) -> None:
    """Print ids of cards with ``repo == <R>`` (one per line; empty when none)."""
    # `scope=""` opts out of the $SCITEX_TODO_SCOPE env default — a producer
    # resolving repo->card must see EVERY matching card, not just its own
    # scope slice.
    cards = _store.list_tasks(
        None,
        scope="",
        repo=repo,
        kind=kind,
        status=status,
    )
    for card in cards:
        cid = card.get("id")
        if cid:
            click.echo(cid)


def register(main: click.Group) -> None:
    """Attach the `emit-event` + `find-card` verbs (+ `resolve-card` alias).

    Renamed in the slice-6b verb-rename pilot: `resolve-card` is a READ
    command (prints ids of cards whose `repo` field matches — exact
    filter lookup, not ranked), which is doctrine §1d's `find` verb
    ("locate objects by name/glob/filter; returns paths or ids").
    `resolve` is also a banned synonym in the terminal-verb table. The
    old name forwards with a once-per-shell stderr warning until v0.9.
    """
    main.add_command(emit_event_cmd, name="emit-event")
    main.add_command(find_card_cmd, name="find-card")
    deprecated_alias(main, "resolve-card", target="find-card", remove_in="0.9")


# EOF
