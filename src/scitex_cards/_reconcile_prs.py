#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic card-freshness automation: auto-close a card once its
linked PR has MERGED.

Most ``in_progress`` / ``blocked`` (and some ``pending``) cards represent
work that ends in a merged PR. The card's ``pr_url`` field is the soft link
(set by the git-link feature / authors). This module scans those cards,
asks GitHub whether the linked PR is merged, and — for the ones that are —
flips the card to ``done`` (reusing :func:`scitex_cards._store.complete_task`)
plus an audit comment. It is the periodic counterpart of the manual
``complete_task`` / ``resolve_task`` verbs so nobody has to hand-update the
board after a merge lands.

Design (mirrors the rest of the package)
----------------------------------------
* **Pure decision core.** :func:`decide_reconcile_action` is a pure
  function over ``(task, merge_state)`` → an action label. No I/O. This is
  what the tests assert against directly.
* **PR-URL parser.** :func:`parse_pr_url` deterministically extracts
  ``(owner, repo, number)`` from the GitHub PR URL shapes we see in the
  field. Unparseable → ``None`` (treated as "unknown → skip").
* **Merge-state seam.** The network call is factored behind a callable
  (``merge_state_fn``) exactly like the package's other entry-point seams
  (``entry_points=`` on the hook dispatch, the watch-ci fault injection).
  The default implementation prefers ``gh pr view`` (the dashboard HOST has
  ``gh``) and falls back to a ``curl`` GitHub REST call. Tests inject a
  fake callable returning ``"merged"`` / ``"open"`` / ``"unknown"`` with
  no network and no mocks.
* **Fail-soft.** Any parse / network / subprocess failure resolves to
  ``"unknown"`` → the card is left untouched. We NEVER wrongly close.
* **Default DRY-RUN.** :func:`reconcile_merged_prs` reports only unless
  ``apply=True`` (mirrors scitex-dev's board-mutation pattern — no silent
  auto-close).

Idempotency: a card already ``done`` is skipped (never re-stamped), and
``complete_task`` is itself idempotent, so a double run is safe.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# Statuses we consider "open work that may have merged". A card outside
# this set (done / deferred / failed / cancelled / goal) is never
# auto-closed — ``cancelled`` is already terminal, so it returns
# ACTION_SKIP_NOT_OPEN like the other closed states. ``deferred`` stays out
# on purpose: parked work must not be closed behind the owner's back by a
# PR that happened to merge. (``pending`` was abolished 2026-07-10.)
OPEN_STATUSES: frozenset[str] = frozenset({"in_progress", "blocked"})

# Merge-state vocabulary the seam returns. "unknown" is the fail-soft value
# for any parse/network error — it NEVER closes a card.
MERGED = "merged"
OPEN = "open"
UNKNOWN = "unknown"

# Action labels the pure decision core returns.
ACTION_CLOSE = "close"  # PR merged + card open → flip to done
ACTION_SKIP_DONE = "skip-done"  # already terminal
ACTION_SKIP_NO_PR = "skip-no-pr"  # no pr_url to check
ACTION_SKIP_NOT_OPEN = "skip-not-open"  # status not in OPEN_STATUSES
ACTION_SKIP_NOT_MERGED = "skip-not-merged"  # PR still open
ACTION_SKIP_UNKNOWN = "skip-unknown"  # merge-state could not be determined

#: GitHub PR URL → owner / repo / number. Accepts the canonical
#: ``https://github.com/<owner>/<repo>/pull/<n>`` and tolerates a trailing
#: ``/files`` etc., a ``.git`` suffix on the repo is NOT expected on a PR
#: URL. ``owner``/``repo`` are the standard GitHub slug shape.
_PR_URL_RE = re.compile(
    r"github\.com[/:]+"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]*)/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?/"
    r"pull/"
    r"(?P<number>\d+)"
)


@dataclass(frozen=True)
class PrRef:
    """A parsed GitHub PR reference."""

    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_pr_url(pr_url: Optional[str]) -> Optional[PrRef]:
    """Parse ``owner`` / ``repo`` / ``number`` out of a GitHub PR URL.

    Returns ``None`` for empty / unrecognizable input (the caller treats a
    ``None`` as "unknown → skip"; we NEVER guess).

    Examples
    --------
    >>> parse_pr_url("https://github.com/foo/bar/pull/42").slug
    'foo/bar'
    >>> parse_pr_url("https://github.com/foo/bar/pull/42").number
    42
    >>> parse_pr_url("http://github.com/o/r/pull/7/files").number
    7
    >>> parse_pr_url("git@github.com:o/r/pull/9") is None
    False
    >>> parse_pr_url("") is None
    True
    >>> parse_pr_url("https://github.com/foo/bar/issues/1") is None
    True
    """
    if not pr_url or not isinstance(pr_url, str):
        return None
    m = _PR_URL_RE.search(pr_url.strip())
    if not m:
        return None
    try:
        number = int(m.group("number"))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return PrRef(owner=m.group("owner"), repo=m.group("repo"), number=number)


def decide_reconcile_action(task: dict, merge_state: str) -> str:
    """Pure decision: given a card + a known merge-state, decide what to do.

    No I/O. This is the auto-close predicate, isolated so a test can assert
    every branch against a fixed merge-state.

    Order of checks (most-terminal first):

    1. already ``done`` (or any non-open status)   → skip.
    2. no / unparseable ``pr_url``                  → skip.
    3. merge-state ``unknown``                      → skip (fail-soft).
    4. PR not merged (``open``)                     → skip.
    5. PR merged + card open                        → close.

    Examples
    --------
    >>> decide_reconcile_action({"status": "in_progress",
    ...     "pr_url": "https://github.com/o/r/pull/1"}, MERGED)
    'close'
    >>> decide_reconcile_action({"status": "done",
    ...     "pr_url": "https://github.com/o/r/pull/1"}, MERGED)
    'skip-done'
    >>> decide_reconcile_action({"status": "in_progress"}, MERGED)
    'skip-no-pr'
    >>> decide_reconcile_action({"status": "in_progress",
    ...     "pr_url": "https://github.com/o/r/pull/1"}, OPEN)
    'skip-not-merged'
    >>> decide_reconcile_action({"status": "in_progress",
    ...     "pr_url": "https://github.com/o/r/pull/1"}, UNKNOWN)
    'skip-unknown'
    """
    status = task.get("status")
    if status == "done":
        return ACTION_SKIP_DONE
    if status not in OPEN_STATUSES:
        return ACTION_SKIP_NOT_OPEN
    if parse_pr_url(task.get("pr_url")) is None:
        return ACTION_SKIP_NO_PR
    if merge_state == MERGED:
        return ACTION_CLOSE
    if merge_state == UNKNOWN:
        return ACTION_SKIP_UNKNOWN
    return ACTION_SKIP_NOT_MERGED


# --------------------------------------------------------------------------- #
# Merge-state seam: gh first, REST (curl) fallback. Fail-soft → "unknown".    #
# --------------------------------------------------------------------------- #
def _gh_merge_state(ref: PrRef, timeout: int = 20) -> Optional[str]:
    """Ask ``gh`` for the PR merge-state. ``None`` when gh is unusable.

    Uses ``gh pr view <owner/repo>#<n> --json state,mergedAt`` (the dashboard
    HOST has ``gh`` authenticated). A merged PR has a non-null ``mergedAt``
    AND ``state == "MERGED"``. Returns :data:`MERGED` / :data:`OPEN`, or
    ``None`` (NOT "unknown") so the caller can try the REST fallback.
    """
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [
                gh,
                "pr",
                "view",
                f"{ref.slug}#{ref.number}",
                "--json",
                "state,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if data.get("mergedAt") or str(data.get("state", "")).upper() == "MERGED":
        return MERGED
    return OPEN


def _rest_merge_state(
    ref: PrRef, token: Optional[str], timeout: int = 20
) -> Optional[str]:
    """REST fallback: ``GET /repos/{owner}/{repo}/pulls/{n}`` → ``.merged``.

    Uses ``curl`` (always present on the host) so we add no Python HTTP dep.
    ``token`` (a GitHub PAT from env) is sent as a bearer when present —
    public repos work tokenless but rate-limit hard. Returns ``None`` on any
    failure so the caller maps it to "unknown".
    """
    curl = shutil.which("curl")
    if not curl:
        return None
    url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}"
    cmd = [curl, "-fsSL", "-H", "Accept: application/vnd.github+json"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    cmd.append(url)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("merged") is True or data.get("merged_at"):
        return MERGED
    return OPEN


def default_merge_state_fn(pr_url: str) -> str:
    """Default seam: gh → REST fallback → :data:`UNKNOWN` (fail-soft).

    Parses the URL, tries ``gh`` first (the dashboard host has it), then the
    ``curl`` REST call (token from ``GH_TOKEN`` / ``GITHUB_TOKEN`` env). Any
    parse / network / subprocess failure resolves to ``"unknown"`` — never a
    wrong "merged".
    """
    import os

    ref = parse_pr_url(pr_url)
    if ref is None:
        return UNKNOWN
    state = _gh_merge_state(ref)
    if state is not None:
        return state
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    state = _rest_merge_state(ref, token)
    if state is not None:
        return state
    return UNKNOWN


# --------------------------------------------------------------------------- #
# Orchestration: scan → decide → (dry-run report | apply mutation).          #
# --------------------------------------------------------------------------- #
@dataclass
class ReconcileResult:
    """Summary of one reconcile pass."""

    applied: bool
    closed: list[dict] = field(default_factory=list)  # cards flipped to done
    would_close: list[dict] = field(default_factory=list)  # dry-run candidates
    skipped: dict[str, int] = field(default_factory=dict)  # action → count

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "closed": self.closed,
            "would_close": self.would_close,
            "skipped": self.skipped,
            "closed_count": len(self.closed),
            "would_close_count": len(self.would_close),
        }


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    )


def reconcile_merged_prs(
    store: str | Path | None = None,
    *,
    apply: bool = False,
    merge_state_fn: Callable[[str], str] = default_merge_state_fn,
    by: str | None = None,
    entry_points: Iterable | None = None,
) -> ReconcileResult:
    """Scan open cards with a ``pr_url``; close the ones whose PR merged.

    Parameters
    ----------
    store
        Store path override (default: standard resolution).
    apply
        ``False`` (default) → DRY-RUN: report candidates, mutate NOTHING.
        ``True`` → flip merged-PR cards to ``done`` + append an audit comment.
    merge_state_fn
        The injectable seam ``(pr_url) -> "merged"|"open"|"unknown"``. The
        default queries gh / REST; tests pass a fake. Only called for cards
        that pass the cheap pre-checks (open status + parseable pr_url).
    by
        Author stamp forwarded to ``complete_task`` / ``comment_task``.
    entry_points
        Optional plugin entry points forwarded to the C6 ``merged``
        card-event :func:`scitex_cards._events.emit` (the in-process
        injection seam from C1/C2). ``None`` (default) reads the real
        ``scitex_cards.hooks`` group; tests pass a concrete fake handler to
        capture the emitted event. Only consulted when ``apply=True`` and a
        card is genuinely closed on this pass.

    Returns
    -------
    ReconcileResult
        ``closed`` (applied) / ``would_close`` (dry-run) entries each carry
        ``{id, pr_url}``; ``skipped`` is an action→count histogram.
    """
    from ._model import load_tasks
    from ._paths import resolve_tasks_path

    resolved = resolve_tasks_path(store)
    result = ReconcileResult(applied=apply)
    skipped: dict[str, int] = {}

    for task in load_tasks(resolved):
        if not isinstance(task, dict):
            continue
        # Cheap pre-checks first so we only hit the network for real
        # candidates (open status + a parseable pr_url).
        pre = decide_reconcile_action(task, UNKNOWN)
        if pre in (ACTION_SKIP_DONE, ACTION_SKIP_NOT_OPEN, ACTION_SKIP_NO_PR):
            skipped[pre] = skipped.get(pre, 0) + 1
            continue

        pr_url = str(task.get("pr_url"))
        try:
            merge_state = merge_state_fn(pr_url)
        except Exception:  # noqa: BLE001 — fail-soft: never wrongly close.
            merge_state = UNKNOWN
        action = decide_reconcile_action(task, merge_state)

        if action != ACTION_CLOSE:
            skipped[action] = skipped.get(action, 0) + 1
            continue

        entry = {"id": task.get("id"), "pr_url": pr_url}
        if not apply:
            result.would_close.append(entry)
            continue
        _apply_close(
            resolved,
            task_id=str(task.get("id")),
            pr_url=pr_url,
            by=by,
            entry_points=entry_points,
        )
        result.closed.append(entry)

    result.skipped = skipped
    return result


def _apply_close(
    resolved: Path,
    *,
    task_id: str,
    pr_url: str,
    by: str | None,
    entry_points: Iterable | None = None,
) -> None:
    """Flip one card to ``done`` + append the auto-close audit comment.

    Reuses the canonical store helpers (``complete_task`` is idempotent; the
    comment is best-effort so a comment glitch never aborts the close).

    C6 (git-link event producers): after a GENUINELY NEW close — i.e. a card
    that ``reconcile_merged_prs`` decided to flip on THIS pass (an already-
    ``done`` card never reaches here, so a second run emits nothing) — also
    emit a canonical ``merged`` card-event onto the bus. Purely additive +
    fail-soft: there is intentionally no consumer yet (C4 dispatcher is a
    separate card), so an emit with no plugin registered is a harmless noop.
    """
    from ._store import comment_task, complete_task

    complete_task(resolved, task_id, by=by)
    text = f"auto-closed {_utc_now_iso()}: linked PR {pr_url} merged"
    try:
        comment_task(resolved, task_id, text=text, by=by, kind="done")
    except Exception:  # noqa: BLE001 — the close already landed; comment is audit-only.
        import logging

        logging.getLogger(__name__).warning(
            "auto-close comment failed for %r", task_id, exc_info=True
        )

    _emit_merged_event(
        task_id=task_id, pr_url=pr_url, actor=by, entry_points=entry_points
    )


def _emit_merged_event(
    *,
    task_id: str,
    pr_url: str,
    actor: str | None,
    entry_points: Iterable | None = None,
) -> None:
    """Emit a ``merged`` card-event for a freshly auto-closed card.

    Fail-soft: any error building the envelope or reaching the bus is
    swallowed so a reconcile run is never broken by event emission. The
    ``repo`` is derived from the PR URL when parseable (best-effort).
    """
    try:
        from ._events import Event, emit

        ref = parse_pr_url(pr_url)
        repo = ref.slug if ref is not None else None
        emit(
            Event.merged(task_id, repo=repo, pr_url=pr_url, actor=actor),
            entry_points=entry_points,
        )
    except Exception:  # noqa: BLE001 — emit must never break the producer
        import logging

        logging.getLogger(__name__).warning(
            "reconcile merged-event emit failed for %r", task_id, exc_info=True
        )


# EOF
