#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft linking from a local git branch to the matching board card.

Phase P3 of the task-driven-feedback epic (card ``tcfb-p3-git-to-card``).
The companion git hooks (``post-commit`` / ``pre-push``) capture local
git mutations onto a card's ROUTE by emitting a canonical ``push`` event
to ``scitex-cards hook push`` (see :mod:`scitex_cards._hooks` for the wire
shape + the idempotent built-in handler).

Linking is **SOFT**: a card is annotated only when a card id is present
in the branch name. Ad-hoc branches (no recognizable card id) are NOT an
error — they just produce no event, so a developer can commit freely on
a throwaway branch without polluting the board.

Branch convention
------------------
The fleet's ``~/.claude/hooks`` enforce a ``<type>/<slug>`` branch shape,
where ``<type>`` is one of ``feat``/``fix``/``chore``/``refactor``/
``docs``/``test``/``perf``. The card-id convention layers on top:

    <type>/<card-id>-<rest>

The card id is the slug after the leading ``<type>/``. Board card ids are
lower-case, hyphen-separated, alphanumeric tokens (e.g. ``tcfb-p3-git-to-card``,
``scitex-io-clew-tracker-wiring``).

:func:`extract_card_id` is the deterministic parser. Because a card id is
itself hyphenated, the whole post-``<type>/`` remainder is treated as the
candidate card id (after trimming a trailing ``/`` segment, if any). A
branch with no ``<type>/`` prefix, or whose remainder is empty / not a
plausible slug, yields ``None`` (ad-hoc — caller stays silent).

Commit-message trailer fallback
-------------------------------
When a branch carries no card id (ad-hoc branch) a developer can still
opt a single commit into the board by adding a ``Card: <id>`` trailer to
the commit message, mirroring git's ``Co-Authored-By:`` trailer style::

    Fix the flaky timer

    Card: tcfb-p3-git-to-card

:func:`extract_card_id_from_message` reads that trailer. The hooks try
the branch first, then fall back to the message trailer.

Examples
--------
>>> extract_card_id("feat/tcfb-p3-git-to-card")
'tcfb-p3-git-to-card'
>>> extract_card_id("fix/scitex-io-clew-tracker-wiring")
'scitex-io-clew-tracker-wiring'
>>> extract_card_id("chore/full-green")
'full-green'
>>> extract_card_id("develop") is None
True
>>> extract_card_id("feat/wip") is None
True
>>> extract_card_id_from_message("Fix it\\n\\nCard: tcfb-p3-git-to-card")
'tcfb-p3-git-to-card'
"""

from __future__ import annotations

import re

#: Branch ``<type>`` prefixes the fleet's git hooks enforce. A branch
#: must start with one of these followed by ``/`` for the remainder to be
#: considered a candidate card id. (Mirrors the set documented in the
#: task brief; keep in sync with ``~/.claude/hooks`` if it grows.)
BRANCH_TYPES = frozenset({"feat", "fix", "chore", "refactor", "docs", "test", "perf"})

#: A plausible card id: lower-case alphanumeric tokens joined by single
#: hyphens (the slug shape board ids use). Anchored so a single bare word
#: (e.g. ``wip``) still matches the shape — single-token branches are
#: rejected separately below, not by this pattern.
_CARD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: ``Card: <id>`` commit-message trailer (case-insensitive on the key,
#: mirroring git's trailer-key matching). Captures the trailing id token.
_CARD_TRAILER_RE = re.compile(
    r"^[ \t]*Card[ \t]*:[ \t]*(?P<id>[a-z0-9][a-z0-9-]*)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_card_id(branch: str | None) -> str | None:
    """Extract the board card id from a ``<type>/<card-id>-<rest>`` branch.

    Parameters
    ----------
    branch : str or None
        The local git branch name (e.g. ``feat/tcfb-p3-git-to-card``).

    Returns
    -------
    str or None
        The card id (the slug after the leading ``<type>/``), or ``None``
        when the branch has no recognizable card id (ad-hoc branch). SOFT
        linking: ``None`` is the normal, non-error signal to skip.

    Notes
    -----
    Deterministic rules, applied in order:

    1. ``None`` / empty / whitespace branch          -> ``None``.
    2. No ``<type>/`` prefix from :data:`BRANCH_TYPES` -> ``None``
       (the branch isn't shaped by the card-id convention).
    3. Take the remainder after the FIRST ``/``. If it contains further
       ``/`` segments, keep only the first segment (defensive — the
       fleet's branch shape is single-segment, but a stray nested ref
       shouldn't leak a slash into the card id).
    4. The candidate must match the card-id slug shape AND contain at
       least one hyphen (a bare single token like ``wip`` is treated as
       ad-hoc, not a card id) -> else ``None``.

    Examples
    --------
    >>> extract_card_id("feat/tcfb-p3-git-to-card")
    'tcfb-p3-git-to-card'
    >>> extract_card_id("refactor/scitex-quality")
    'scitex-quality'
    >>> extract_card_id("feat/wip") is None
    True
    >>> extract_card_id("main") is None
    True
    >>> extract_card_id("") is None
    True
    """
    if not branch:
        return None
    branch = branch.strip()
    if not branch or "/" not in branch:
        return None

    prefix, _, remainder = branch.partition("/")
    if prefix not in BRANCH_TYPES:
        return None

    # Defensive: keep only the first path segment of the remainder so a
    # nested ref can never smuggle a "/" into a card id.
    candidate = remainder.split("/", 1)[0].strip()
    if not candidate:
        return None

    # A bare single token (no hyphen) is an ad-hoc slug, not a card id.
    if "-" not in candidate:
        return None
    if not _CARD_ID_RE.match(candidate):
        return None
    return candidate


def extract_card_id_from_message(message: str | None) -> str | None:
    """Extract a card id from a ``Card: <id>`` commit-message trailer.

    The fallback source when the branch carries no card id. Mirrors git's
    trailer convention (``Key: value`` on its own line). The LAST matching
    trailer wins, consistent with git's "last trailer of a kind" rule.

    Parameters
    ----------
    message : str or None
        The full commit message (subject + body).

    Returns
    -------
    str or None
        The trailer's card id, or ``None`` when no ``Card:`` trailer is
        present (or the message is empty).

    Examples
    --------
    >>> extract_card_id_from_message("Subject\\n\\nCard: tcfb-p3-git-to-card")
    'tcfb-p3-git-to-card'
    >>> extract_card_id_from_message("card: scitex-quality")
    'scitex-quality'
    >>> extract_card_id_from_message("no trailer here") is None
    True
    >>> extract_card_id_from_message("") is None
    True
    """
    if not message:
        return None
    matches = _CARD_TRAILER_RE.findall(message)
    if not matches:
        return None
    return matches[-1]


def resolve_card_id(branch: str | None, message: str | None = None) -> str | None:
    """Resolve a card id from the branch, falling back to the message trailer.

    The single entry point the git hooks call: try :func:`extract_card_id`
    on the branch first (the common path), then
    :func:`extract_card_id_from_message` on the commit message (the
    explicit opt-in for ad-hoc branches). Returns ``None`` when neither
    source yields a card id -> SOFT skip.

    Examples
    --------
    >>> resolve_card_id("feat/tcfb-p3-git-to-card")
    'tcfb-p3-git-to-card'
    >>> resolve_card_id("wip", "Quick fix\\n\\nCard: scitex-quality")
    'scitex-quality'
    >>> resolve_card_id("wip", "no trailer") is None
    True
    """
    from_branch = extract_card_id(branch)
    if from_branch is not None:
        return from_branch
    return extract_card_id_from_message(message)


#: The two git mutations the hooks distinguish. ``post-commit`` passes
#: :data:`TRIGGER_COMMIT`; ``pre-push`` passes :data:`TRIGGER_PUSH`. The
#: built-in ``push`` handler maps the trigger to the canonical card-event
#: type it emits (commit → ``committed``, push → ``pushed``). The default
#: is :data:`TRIGGER_PUSH` so an older producer that doesn't set the field
#: keeps the historical behaviour (a ``pushed`` event).
TRIGGER_COMMIT = "commit"
TRIGGER_PUSH = "push"
VALID_TRIGGERS = frozenset({TRIGGER_COMMIT, TRIGGER_PUSH})


def build_push_event(
    *,
    repo: str,
    branch: str,
    commit_sha: str,
    author: str | None = None,
    message: str | None = None,
    card_id: str | None = None,
    trigger: str = TRIGGER_PUSH,
) -> dict | None:
    """Assemble a canonical ``push`` event for ``scitex-cards hook push``.

    Resolves the card id (branch, then ``Card:`` message-trailer fallback)
    unless one is passed explicitly, then builds the wire dict consumed by
    :func:`scitex_cards._hooks.event_validate`.

    Returns ``None`` when no card id can be resolved (SOFT skip: the git
    hook emits nothing and never calls the consumer) OR when any required
    field (``repo`` / ``branch`` / ``commit_sha``) is empty.

    The returned dict is the exact payload the ``push`` verb expects::

        {"kind": "push", "repo", "branch", "commit_sha",
         "author", "message", "trigger", "card_ids": [<card_id>]}

    ``trigger`` (``"commit"`` from ``post-commit`` / ``"push"`` from
    ``pre-push``, default ``"push"``) is carried through so the built-in
    handler can emit the right canonical card-event type (``committed`` vs
    ``pushed``). It is ADDITIVE — older consumers that ignore the field see
    the unchanged ``push`` comment behaviour. An unrecognised value is
    coerced to :data:`TRIGGER_PUSH` (fail-soft; the field is a hint, never
    a hard contract).

    Examples
    --------
    >>> build_push_event(
    ...     repo="owner/repo", branch="feat/tcfb-p3-git-to-card",
    ...     commit_sha="abc123", author="me", message="msg",
    ... )["card_ids"]
    ['tcfb-p3-git-to-card']
    >>> build_push_event(
    ...     repo="owner/repo", branch="feat/some-card",
    ...     commit_sha="abc123", trigger="commit",
    ... )["trigger"]
    'commit'
    >>> build_push_event(
    ...     repo="owner/repo", branch="wip", commit_sha="abc123",
    ... ) is None
    True
    """
    if not (repo and branch and commit_sha):
        return None
    resolved = card_id if card_id is not None else resolve_card_id(branch, message)
    if not resolved:
        return None
    return {
        "kind": "push",
        "repo": repo,
        "branch": branch,
        "commit_sha": commit_sha,
        "author": author,
        "message": message,
        "trigger": trigger if trigger in VALID_TRIGGERS else TRIGGER_PUSH,
        "card_ids": [resolved],
    }


def _main(argv: list[str] | None = None) -> int:
    """CLI seam for the git hooks. Two sub-modes, both best-effort (exit 0).

    ``python -m scitex_cards._git_link card-id <branch> [<commit-msg-file>]``
        Resolve the card id (branch, then ``Card:`` trailer in the message
        file) and print it to stdout. Prints NOTHING when none is found.

    ``python -m scitex_cards._git_link emit-event \\``
        ``--repo R --branch B --sha S [--author A] [--message-file F]``
        ``[--trigger commit|push]``
        Resolve the card id and print the full canonical push-event JSON to
        stdout (one line). Prints NOTHING when no card id resolves -> the
        shell hook treats empty output as a SOFT skip and never calls the
        consumer. ``--trigger`` (default ``push``) tells the consumer
        whether this came from ``post-commit`` (``commit``) or ``pre-push``
        (``push``) so it emits the matching canonical card-event type.

    Always returns 0 (a parse glitch must never break a commit/push).
    """
    import argparse
    import json
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 0
    mode, rest = args[0], args[1:]

    def _read(path: str | None) -> str | None:
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    if mode == "card-id":
        branch = rest[0] if rest else None
        message = _read(rest[1] if len(rest) > 1 else None)
        card_id = resolve_card_id(branch, message)
        if card_id:
            print(card_id)
        return 0

    if mode == "emit-event":
        parser = argparse.ArgumentParser(prog="scitex_cards._git_link emit-event")
        parser.add_argument("--repo", default="")
        parser.add_argument("--branch", default="")
        parser.add_argument("--sha", default="")
        parser.add_argument("--author", default=None)
        parser.add_argument("--message-file", default=None)
        parser.add_argument(
            "--trigger",
            choices=sorted(VALID_TRIGGERS),
            default=TRIGGER_PUSH,
        )
        ns = parser.parse_args(rest)
        event = build_push_event(
            repo=ns.repo,
            branch=ns.branch,
            commit_sha=ns.sha,
            author=ns.author,
            message=_read(ns.message_file),
            trigger=ns.trigger,
        )
        if event is not None:
            print(json.dumps(event))
        return 0

    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI dispatch
    raise SystemExit(_main())


# EOF
