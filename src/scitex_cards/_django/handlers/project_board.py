#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PROJECT-SCOPED board — the ordinary-user surface of the Cards leaf.

WHAT THIS PAGE IS FOR, and why it is a SECOND page rather than a rewrite of the
operator board. ``/`` is the fleet board: the whole store, every agent's cards,
the operator's own instrument. Hub PR #923 (Private Beta login-to-wow) requires
a different thing behind ``/apps/cards/`` for an ordinary signed-in user — "a
project-scoped board MVP: current project, canonical picker, authorized cards
only ... server-side tenant isolation; ordinary users never see fleet/global
cards." Those two are not the same page with a filter on it: the fleet board's
whole value is that it is NOT scoped, and the MVP's whole value is that it is.
So the fleet board keeps ``/`` and this module owns the project-scoped page.

WHAT IS CONSUMED RATHER THAN REBUILT (Hub PR #923 ownership matrix — the SDK
owns the canonical project selector, ``scitex-cards`` owns "project board domain
and tenancy"):

  * the PICKER is ``{% scitex_project_picker %}`` from scitex-ui, and the
    precedence that decides which project opens is scitex-ui's
    :func:`scitex_ui.project_scope.resolve_project` — explicit project wins,
    an inaccessible explicit project resolves to None and NEVER silently falls
    back to the stored one, otherwise the last visited project if still
    accessible, otherwise None. Re-implementing that here would be the second
    selector the SDK exists to prevent.
  * the USER-ROW tenancy predicate is :func:`scope_rows_for_user` from
    :mod:`scitex_cards._user_row_scope` (the 0.52 P0 leak's fix), not a new one.

WHAT THIS MODULE ADDS is the composition: a row is on this page iff the viewer
may see it AND it belongs to the current project. And, because the two failures
are different, the composition is done in that ORDER — tenancy first, project
second — so a project id the viewer cannot access can never reveal even the
COUNT of another tenant's cards.

FAIL CLOSED, ALWAYS, NO FLAG. The fleet board's scoping sits behind
``SCITEX_CARDS_USER_SCOPE`` because turning it on changes an existing page under
a live operator. This page has no such history: it exists for ordinary users, so
every path that cannot prove who the viewer is and which project they may see
renders a refusal instead of a list. A flag here would be a way to ship the
MVP with the tenancy off, which is the one way it must never ship.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence
from uuid import uuid4

logger = logging.getLogger(__name__)

#: The page's terminal states. Each is DISTINCT because the operator-facing
#: contract in Hub PR #923 requires them to be, and because they need different
#: actions from the reader: pick a project / ask for access / retry / create the
#: first card / clear the filters. Collapsing any two of them produces a page
#: that lies about which of those the reader should do.
NO_PROJECT = "no-project"
DENIED = "denied"
UNAVAILABLE = "unavailable"
EMPTY = "empty"
FILTERED_EMPTY = "filtered-empty"
READY = "ready"

#: Server-side session key for "the project this viewer visited last". The SDK's
#: provider protocol asks for it; the store is not the right home for it (it is
#: a per-browser preference, and the store is shared by the whole fleet).
SESSION_KEY = "scitex_cards_last_project"

#: Query parameters this page honours. ``project`` is the SDK's own name for the
#: explicit selection (:data:`scitex_ui.project_scope.PROJECT_QUERY_PARAM`); the
#: other three are the filters Hub PR #923 asks for.
PARAM_PROJECT = "project"
PARAM_Q = "q"
PARAM_STATUS = "status"
PARAM_ASSIGNEE = "assignee"
PARAM_OFFSET = "offset"

#: How many cards one page shows. Imported from the query module rather than
#: restated, because the SQL's LIMIT and the paging arithmetic must agree: a clamp
#: that used a different page size than the query would jump to a page boundary
#: the database never produces.
from ..._project_board_query import DEFAULT_ROW_LIMIT  # noqa: E402  (kept beside its use)

#: The row field a card's project lives in, named once so a rename is one edit.
PROJECT_FIELD = "project"


@dataclass(frozen=True)
class BoardState:
    """Everything the template needs, and nothing that needs the store again."""

    state: str
    principal: str
    is_staff: bool
    project: Optional[str] = None
    projects: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    total: int = 0
    offset: int = 0
    page_size: int = 0
    filters: dict = field(default_factory=dict)
    detail: str = ""

    @property
    def filters_active(self) -> bool:
        return any(bool(v) for v in self.filters.values())

    @property
    def has_prev(self) -> bool:
        """True when this page is not the first — the reader can go back."""
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        """True when the project holds cards past this page.

        ``total`` is a COUNT from the store, not the length of what was fetched:
        a bounded page that reported its own length as the total would say
        "500 of 500" for a project holding 1,200 cards, which is not a bounded
        view but a wrong one.
        """
        return (self.offset + len(self.rows)) < self.total

    @property
    def shown_from(self) -> int:
        """1-based index of the first card on this page (0 when there are none)."""
        return self.offset + 1 if self.rows else 0

    @property
    def shown_to(self) -> int:
        """1-based index of the last card on this page."""
        return self.offset + len(self.rows)


def projects_of(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The distinct, non-empty project ids present in ``rows``, sorted.

    Sorted rather than first-seen: a picker whose order changes between two
    renders of the same data is a picker the reader cannot build muscle memory
    for.
    """
    found = {
        str(row[PROJECT_FIELD]).strip()
        for row in rows
        if str(row.get(PROJECT_FIELD) or "").strip()
    }
    return tuple(sorted(found))


def rows_of_project(rows: Sequence[Mapping[str, Any]], project: str) -> list[Mapping[str, Any]]:
    """The subset of ``rows`` that belongs to ``project`` (exact match)."""
    return [row for row in rows if str(row.get(PROJECT_FIELD) or "").strip() == project]


def apply_filters(
    rows: Sequence[Mapping[str, Any]],
    *,
    q: str = "",
    status: str = "",
    assignee: str = "",
) -> list[Mapping[str, Any]]:
    """Filter ``rows`` by the three MVP filters, server-side.

    Server-side on purpose: a client-side filter over a payload that already
    contains every row of every project would defeat the tenancy above it —
    "only authorized cards" has to mean only authorized cards are SENT.

    ``q`` is a case-insensitive substring of the title, which is what a reader
    expects from a search box and what the board's own search box does. It is
    not a query language and does not pretend to be one.
    """
    needle = (q or "").strip().casefold()
    wanted_status = (status or "").strip()
    wanted_assignee = (assignee or "").strip()
    out = []
    for row in rows:
        if needle and needle not in str(row.get("title") or "").casefold():
            continue
        if wanted_status and str(row.get("status") or "") != wanted_status:
            continue
        if wanted_assignee and str(row.get("assignee") or "") != wanted_assignee:
            continue
        out.append(row)
    return out


class SessionProjectProvider:
    """The SDK's ``ProjectProvider`` protocol, backed by the viewer's own rows.

    ``list_projects`` answers "which projects may THIS request's viewer see" —
    in the cards domain that is exactly the set of projects its authorized rows
    name, which is why this belongs here and not in the hub (the hub's provider
    answers from its own database and registers itself as
    ``SCITEX_PROJECT_PROVIDER``; a leaf does not need to know which).

    ``last_visited``/``remember`` keep the last project in the session, not in
    the store: the store is shared by the whole fleet and a browser's last
    visited project is not a fact about the fleet.
    """

    def __init__(self, entries: Sequence[str]):
        self._entries = tuple(entries)

    def list_projects(self, request: Any = None):  # noqa: D102 - protocol method
        from scitex_ui.project_scope import ProjectEntry

        return [ProjectEntry(id=pid, name=pid) for pid in self._entries]

    def last_visited(self, request: Any = None) -> Optional[str]:  # noqa: D102
        session = getattr(request, "session", None)
        if session is None:
            return None
        value = session.get(SESSION_KEY)
        return str(value) if value else None

    def remember(self, request: Any, project_id: str) -> None:  # noqa: D102
        session = getattr(request, "session", None)
        if session is None:
            return
        session[SESSION_KEY] = project_id


def _resolve_current(request: Any, provider: SessionProjectProvider, explicit: str) -> Optional[str]:
    """scitex-ui's precedence, called rather than re-derived.

    Imported lazily: this module is imported by ``urls.py`` at Django startup,
    and the fleet board must keep working on a deployment whose scitex-ui
    predates the project-scope API (the same graceful-degradation rule
    ``settings.py`` applies to the element inspector).
    """
    from scitex_ui.project_scope import resolve_project

    return resolve_project(request, provider, explicit=explicit or None)


def _offset_from(params: Mapping[str, Any]) -> int:
    """The page start the request asked for, or 0.

    A junk offset is treated as the first page rather than refused: this is a LINK
    target a reader can edit by hand, and the honest answer to "?offset=banana" is
    the board, not an error page.
    """
    raw = str(params.get(PARAM_OFFSET) or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def _clamp_offset(offset: int, total: int, limit: int = DEFAULT_ROW_LIMIT) -> int:
    """The nearest valid page start at or before ``offset``.

    Clamping rather than refusing matters for the case that actually happens: a
    bookmarked page N of a project that has since shrunk would otherwise render an
    empty board, which a reader reads as "my project is empty" rather than "that
    page is gone".
    """
    if offset <= 0 or total <= 0:
        return 0
    if offset < total:
        return offset
    return max(0, ((total - 1) // limit) * limit)


def authorized_rows(
    rows: Sequence[Mapping[str, Any]],
    principal: str,
    *,
    is_staff: bool = False,
) -> list[Mapping[str, Any]]:
    """The rows this viewer may see AT ALL, before any project is applied.

    This is the fail-closed seam, named rather than inlined so it can be tested
    as itself: an empty principal with no staff bypass returns NOTHING — not an
    empty list of projects (which would still confirm the store has projects),
    and not an unfiltered set (which is the P0 this predicate's sibling fixed).
    """
    from ..._user_row_scope import scope_rows_for_user

    if not is_staff and not principal:
        return []
    return list(scope_rows_for_user(rows, principal, is_staff=is_staff))


def _default_projects_loader(request: Any) -> list[str]:
    """The viewer's project list, from ONE indexed DISTINCT query.

    Not the store document: this is what replaced a 15.6s whole-store read
    (measured) with a query the schema already indexes (`idx_tasks_project`,
    `idx_tasks_assignee`, `idx_tasks_agent`).
    """
    from .._user_scope import current_user
    from .._request_store import read_store
    from ..._project_board_query import projects_for

    principal = current_user(request) or ""
    is_staff = bool(getattr(getattr(request, "user", None), "is_staff", False))
    return projects_for(principal, store=read_store(request), is_staff=is_staff)


def _default_rows_loader(request: Any, project: str, offset: int = 0) -> list[dict]:
    """ONE page of ONE project's authorized cards, bounded fields — never the graph."""
    from .._user_scope import current_user
    from .._request_store import read_store
    from ..._project_board_query import rows_for

    principal = current_user(request) or ""
    is_staff = bool(getattr(getattr(request, "user", None), "is_staff", False))
    return rows_for(
        project, principal, store=read_store(request), is_staff=is_staff, offset=offset
    )


def _default_count_loader(request: Any, project: str) -> int:
    """The project's real card count for this viewer — one indexed aggregate.

    Without it the page could only report how many rows it happened to fetch, and
    a bounded page reporting its own bound as the total is a page that lies about
    the board.
    """
    from .._user_scope import current_user
    from .._request_store import read_store
    from ..._project_board_query import count_for

    principal = current_user(request) or ""
    is_staff = bool(getattr(getattr(request, "user", None), "is_staff", False))
    return count_for(project, principal, store=read_store(request), is_staff=is_staff)


def board_state(
    request: Any,
    *,
    projects_loader: Optional[Callable[[Any], Sequence[str]]] = None,
    rows_loader: Optional[Callable[[Any, str, int], Sequence[dict]]] = None,
    count_loader: Optional[Callable[[Any, str], int]] = None,
    provider: Optional[Any] = None,
) -> BoardState:
    """Decide which of the six states this request is in, and with what rows.

    TWO LOADERS, not one, and that is the performance fix as much as the
    structure: phase 1 asks only for the viewer's PROJECT IDS (a DISTINCT query),
    phase 2 asks only for the SELECTED project's cards. The previous shape loaded
    the whole store to answer both questions and measured 72.7s cold / 32.7s warm
    on the shared fleet; the Hub gate for this page is <=3s cold / <=1.5s warm.

    Both loaders are injectable so the tenancy and the state machine stay testable
    hermetically — no database, no Django request machinery — which is the same
    reason ``_user_row_scope`` takes loaded rows rather than a DSN.

    ``provider`` is injectable for the deployment where the HOST answers "which
    projects may this viewer see" (its own database, its own sharing rules). That
    is the case where a project can be accessible with no cards in the store yet,
    which is exactly the state this page must render as EMPTY rather than as
    "you have access to nothing".
    """
    from .._user_scope import current_user

    principal = current_user(request) or ""
    user = getattr(request, "user", None)
    is_staff = bool(getattr(user, "is_staff", False)) if user is not None else False

    params = getattr(request, "GET", None) or {}
    explicit = str(params.get(PARAM_PROJECT) or "").strip()
    filters = {
        PARAM_Q: str(params.get(PARAM_Q) or ""),
        PARAM_STATUS: str(params.get(PARAM_STATUS) or ""),
        PARAM_ASSIGNEE: str(params.get(PARAM_ASSIGNEE) or ""),
    }

    # TENANCY FIRST, BEFORE ANY LOOKUP. A viewer the boundary cannot name sees
    # nothing at all — and, just as important, this page never runs a query it
    # has no principal for.
    if not is_staff and not principal:
        return BoardState(state=DENIED, principal=principal, is_staff=is_staff, filters=filters)

    list_projects = projects_loader or _default_projects_loader
    load_rows = rows_loader or _default_rows_loader
    count_loader_default = count_loader or _default_count_loader
    offset = _offset_from(params)
    try:
        projects = tuple(list_projects(request))
    except Exception as exc:  # noqa: BLE001 - every load failure is ONE state to the reader
        logger.warning("[scitex-cards] project board: store unavailable: %s", exc, exc_info=True)
        return BoardState(
            state=UNAVAILABLE,
            principal=principal,
            is_staff=is_staff,
            filters=filters,
            detail=str(exc)[:200],
        )

    active_provider = provider if provider is not None else SessionProjectProvider(projects)
    current = _resolve_current(request, active_provider, explicit)

    # The project was named but is not one this viewer may see. scitex-ui's
    # precedence deliberately resolves that to None; the page must NOT then fall
    # back to the last visited project, because the reader asked for something
    # else and showing them a different project is how a tenancy bug looks from
    # the outside.
    if explicit and current is None:
        return BoardState(
            state=DENIED,
            principal=principal,
            is_staff=is_staff,
            projects=projects,
            filters=filters,
        )

    if current is None:
        return BoardState(
            state=NO_PROJECT,
            principal=principal,
            is_staff=is_staff,
            projects=projects,
            filters=filters,
        )

    try:
        total = int(count_loader_default(request, current))
    except Exception as exc:  # noqa: BLE001 - same single answer as above
        logger.warning("[scitex-cards] project board: store unavailable: %s", exc, exc_info=True)
        return BoardState(
            state=UNAVAILABLE,
            principal=principal,
            is_staff=is_staff,
            project=current,
            projects=projects,
            filters=filters,
            detail=str(exc)[:200],
        )

    # THE OFFSET IS CLAMPED BEFORE THE PAGE IS FETCHED, against the store's own
    # count: ?offset=99999 on a 3-card project would otherwise render an empty
    # board with a "next" link, which reads as "your project is empty".
    offset = _clamp_offset(offset, total)

    try:
        loaded = list(load_rows(request, current, offset))
    except Exception as exc:  # noqa: BLE001 - same single answer as above
        logger.warning("[scitex-cards] project board: store unavailable: %s", exc, exc_info=True)
        return BoardState(
            state=UNAVAILABLE,
            principal=principal,
            is_staff=is_staff,
            project=current,
            projects=projects,
            filters=filters,
            detail=str(exc)[:200],
        )

    # DEFENCE IN DEPTH, not the primary rule: the SQL carries the same predicate
    # (built from ``OWNED_FIELDS``, the one definition), and this re-applies it in
    # memory so a future loader swap cannot quietly widen what the page shows.
    project_rows = authorized_rows(rows_of_project(loaded, current), principal, is_staff=is_staff)

    if not project_rows and total == 0:
        return BoardState(
            state=EMPTY,
            principal=principal,
            is_staff=is_staff,
            project=current,
            projects=projects,
            filters=filters,
        )

    visible = apply_filters(project_rows, **filters)
    if not visible:
        return BoardState(
            state=FILTERED_EMPTY,
            principal=principal,
            is_staff=is_staff,
            project=current,
            projects=projects,
            total=total,
            offset=offset,
            page_size=len(project_rows),
            filters=filters,
        )

    return BoardState(
        state=READY,
        principal=principal,
        is_staff=is_staff,
        project=current,
        projects=projects,
        rows=tuple(visible),
        total=total,
        offset=offset,
        page_size=len(project_rows),
        filters=filters,
    )


#: The status that REQUIRES a gate. Not named in the page's own words after this:
#: the store's validator is the authority (see ``canonical_blockers``), and this
#: constant is the one place the page agrees with it about which status is gated.
BLOCKED_STATUS = "blocked"

#: The refusal a reader gets for a gated status with no gate. It says what to do,
#: because a refusal a reader cannot act on is only half a refusal.
BLOCKED_NEEDS_GATE = (
    "A blocked card must name its gate, so whoever can clear it knows what to do. "
    "Choose a blocker, or use a status that reflects reality."
)


def canonical_blockers() -> tuple[str, ...]:
    """The store's own blocker vocabulary, asked for rather than restated.

    This is the list the writer's validator checks; a page-local copy would be a
    second list to forget. The page needs it because of a defect the CI log handed
    me: a create through this form could write a card with status 'blocked' and NO
    gate, which the store accepts with a warning — so the GUI could file work the
    fleet's own validator calls incomplete.
    """
    from ..._task import VALID_BLOCKERS

    return tuple(VALID_BLOCKERS)


def _blocker_or_error(form: Mapping[str, Any]) -> tuple[Optional[str], str]:
    """The blocker the form asked for, validated against the canonical set."""
    raw = str(form.get("blocker") or "").strip()
    if not raw:
        return None, ""
    if raw not in canonical_blockers():
        return None, f"{raw!r} is not a blocker this store has."
    return raw, ""


def canonical_statuses() -> tuple[str, ...]:
    """The store's OWN status vocabulary, asked for rather than restated.

    The page's columns are the canonical lifecycle, so a status added to the
    store shows up here without a second list to forget to update — the same
    single-source rule the board's status COLOURS follow.
    """
    from ..._store import VALID_STATUSES

    return tuple(VALID_STATUSES)


#: A new card on a project board starts IN FLIGHT rather than deferred: the
#: store's own default ("deferred") is the right default for a harvested backlog
#: and the wrong one for something a person just typed into a board.
CREATE_DEFAULT_STATUS = "in_progress"

#: Longest title accepted. Not a store limit (the store has none) — a page limit,
#: because a title is a label on a card and a 4 KB label is a bug report waiting
#: to happen. Chosen to fit a phone column without truncation games.
CREATE_TITLE_MAX = 200


@dataclass(frozen=True)
class CreateResult:
    """What happened when someone pressed Create."""

    ok: bool
    card_id: Optional[str] = None
    error: str = ""


def _slug(text: str) -> str:
    """A URL- and id-safe slug, ASCII-only, hyphenated.

    Ids on this board are human-readable (``board-graph-ids-collide-...``), and a
    card created from a page should join that convention rather than arrive as a
    uuid nobody can read in a graph label.
    """
    kept = [c if (c.isalnum() and c.isascii()) else "-" for c in text.lower()]
    slug = "".join(kept)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or "card"


def create_card(
    state: BoardState,
    form: Mapping[str, Any],
    *,
    add: Optional[Callable[..., dict]] = None,
    store: Any = None,
    now: Optional[str] = None,
) -> CreateResult:
    """Create ONE card inside the current project, or refuse with a reason.

    THE FORM CANNOT CHOOSE WHO OR WHERE. ``project``, ``created_by`` and ``agent``
    are taken from the resolved state and the principal — never from the POST
    body — so a crafted request cannot file a card into another tenant's project
    or sign it with someone else's name. That is the whole reason this function
    takes the resolved ``state`` rather than a request: by the time it runs, the
    tenancy question has already been answered once and cannot be re-answered by
    the client.

    Refusals are STATES rather than exceptions because every one of them is
    something the reader can fix: they typed no title, chose a status the store
    does not have, or are looking at a page with no project to file into.
    """
    if state.state in (DENIED, UNAVAILABLE, NO_PROJECT):
        return CreateResult(ok=False, error="There is no project to file this card into.")

    title = str(form.get("title") or "").strip()
    if not title:
        return CreateResult(ok=False, error="A card needs a title.")
    if len(title) > CREATE_TITLE_MAX:
        return CreateResult(ok=False, error=f"A title can be up to {CREATE_TITLE_MAX} characters.")

    status = str(form.get("status") or "").strip() or CREATE_DEFAULT_STATUS
    if status not in canonical_statuses():
        return CreateResult(ok=False, error=f"{status!r} is not a status this store has.")

    blocker, blocker_error = _blocker_or_error(form)
    if blocker_error:
        return CreateResult(ok=False, error=blocker_error)
    if status == BLOCKED_STATUS and not blocker:
        return CreateResult(ok=False, error=BLOCKED_NEEDS_GATE)

    assignee = str(form.get("assignee") or "").strip() or state.principal
    priority_raw = str(form.get("priority") or "").strip()
    if priority_raw:
        try:
            priority: Optional[int] = int(priority_raw)
        except ValueError:
            return CreateResult(ok=False, error="Priority must be a whole number.")
    else:
        priority = None

    # A suffix, because two cards may legitimately share a title and the store
    # keys on the id. A timestamp plus four hex characters keeps the id readable
    # AND collision-resistant without an existence query over the whole fleet.
    stamp = (now or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))[-14:]
    card_id = f"{_slug(title)}-{stamp}-{uuid4().hex[:4]}"

    writer = add or _default_add
    try:
        written = writer(
            store=store,
            id=card_id,
            title=title,
            status=status,
            blocker=blocker,
            project=state.project,
            assignee=assignee,
            priority=priority,
            created_by=state.principal,
            agent=state.principal,
        )
    except Exception as exc:  # noqa: BLE001 - one answer for every write failure
        logger.warning("[scitex-cards] project board create failed: %s", exc, exc_info=True)
        return CreateResult(ok=False, error="The card could not be saved. Try again.")
    return CreateResult(ok=True, card_id=str(written.get("id") or card_id))


def _default_add(**fields: Any) -> dict:
    """Write through the store API — never SQL, never a hand-edit.

    The board's second mandate is that the store is mutated only through its own
    API, which validates the row, races correctly with other writers and leaves
    the audit trail. A GUI is not an exception to that; it is the case the rule
    was written for.
    """
    from ..._store import add_task

    return add_task(**fields)


@dataclass(frozen=True)
class UpdateResult:
    """What happened when someone changed a card from the board."""

    ok: bool
    card_id: Optional[str] = None
    error: str = ""


#: The fields this page will change on an existing card, and nothing else. The
#: board's inline controls offer status / assignee / priority; the card's own page
#: adds the two fields that need room to type (title, note). A form cannot name
#: anything outside this set, so the page is a board with an editor, not a
#: general-purpose store editor wearing a board's styling.
UPDATABLE_FIELDS: tuple[str, ...] = ("status", "assignee", "priority", "title", "note")

#: Longest note accepted, for the same reason as the title limit: a note is a
#: human's paragraph, and a 1 MB note is a paste accident.
NOTE_MAX = 4000

#: Sending a card here is how "archive" is spelled on this board: the store keeps
#: the row (nothing is ever deleted — house rule) and the board stops showing it
#: as work in flight.
ARCHIVE_STATUS = "cancelled"


def update_card(
    state: BoardState,
    form: Mapping[str, Any],
    *,
    update: Optional[Callable[..., dict]] = None,
    store: Any = None,
) -> UpdateResult:
    """Change ONE card that is already visible on this board, or refuse.

    THE CARD MUST BE IN THE STATE THE PAGE RENDERED. ``card_id`` comes from the
    form, so it is the one thing an attacker controls; it is therefore not trusted
    for anything except a LOOKUP into ``state.rows`` — the rows the page already
    resolved through the SQL tenancy predicate. A card id from another tenant, or
    from another project, is simply not in that set, and the update is refused
    BEFORE the store is opened. That is a stronger check than re-querying the
    store with the id, because it cannot be satisfied by a row the page would not
    have shown.

    Refusals are STATES rather than exceptions for the same reason as create: each
    one is something the reader can fix.
    """
    if state.state != READY:
        return UpdateResult(ok=False, error="This project's board is not open, so no card can be changed.")

    card_id = str(form.get("card_id") or "").strip()
    if not card_id:
        return UpdateResult(ok=False, error="No card was named.")
    known = {str(row.get("id")) for row in state.rows}
    if card_id not in known:
        # Deliberately the same sentence whether the card belongs to another
        # tenant, another project, or does not exist: the page must not become an
        # oracle for what is out there.
        return UpdateResult(ok=False, error="That card is not on this board.")
    # The ROW is needed as well as the id, because the blocker rule is about what
    # the card will say AFTER the change: a card that is already blocked and keeps
    # its gate must not be refused for "having no blocker" it never lost.
    current = next((row for row in state.rows if str(row.get("id")) == card_id), None)

    changes: dict = {}
    status = str(form.get("status") or "").strip()
    if status:
        if status not in canonical_statuses():
            return UpdateResult(ok=False, error=f"{status!r} is not a status this store has.")
        changes["status"] = status

    blocker, blocker_error = _blocker_or_error(form)
    if blocker_error:
        return UpdateResult(ok=False, error=blocker_error)
    if blocker:
        changes["blocker"] = blocker

    if changes.get("status") == BLOCKED_STATUS:
        existing = str((current or {}).get("blocker") or "").strip()
        if not (blocker or existing):
            return UpdateResult(ok=False, error=BLOCKED_NEEDS_GATE)

    if "assignee" in form:
        assignee = str(form.get("assignee") or "").strip()
        if assignee:
            changes["assignee"] = assignee

    if "priority" in form:
        priority_raw = str(form.get("priority") or "").strip()
        if priority_raw:
            try:
                changes["priority"] = int(priority_raw)
            except ValueError:
                return UpdateResult(ok=False, error="Priority must be a whole number.")

    if "title" in form:
        title = str(form.get("title") or "").strip()
        if not title:
            return UpdateResult(ok=False, error="A card needs a title.")
        if len(title) > CREATE_TITLE_MAX:
            return UpdateResult(
                ok=False, error=f"A title can be up to {CREATE_TITLE_MAX} characters."
            )
        changes["title"] = title

    if "note" in form:
        note = str(form.get("note") or "")
        if len(note) > NOTE_MAX:
            return UpdateResult(ok=False, error=f"A note can be up to {NOTE_MAX} characters.")
        # An EMPTY note is sent deliberately here, unlike assignee above: clearing
        # a note is a thing people mean to do, while clearing an assignee by
        # accident is how a card stops being anybody's.
        changes["note"] = note

    if not changes:
        return UpdateResult(ok=False, error="Nothing was changed.")

    writer = update or _default_update
    try:
        writer(store=store, task_id=card_id, **changes)
    except Exception as exc:  # noqa: BLE001 - one answer for every write failure
        logger.warning("[scitex-cards] project board update failed: %s", exc, exc_info=True)
        return UpdateResult(ok=False, error="The card could not be saved. Try again.")
    return UpdateResult(ok=True, card_id=card_id)


def _default_update(**fields: Any) -> dict:
    """Mutate through the store API, for the same reasons as ``_default_add``."""
    from ..._store import update_task

    return update_task(**fields)


#: Longest comment accepted. A comment is a note to the next reader, not a document.
COMMENT_MAX = 2000


@dataclass(frozen=True)
class CommentResult:
    """What happened when someone commented from the card page."""

    ok: bool
    card_id: Optional[str] = None
    error: str = ""


def comment_card(
    state: BoardState,
    form: Mapping[str, Any],
    *,
    comment: Optional[Callable[..., dict]] = None,
    store: Any = None,
) -> CommentResult:
    """Append a comment to ONE card that is already on this board, or refuse.

    Same boundary as :func:`update_card`, for the same reason: the card id comes
    from the form, so it is only ever a LOOKUP key into the rows the page already
    resolved under the tenancy predicate. The AUTHOR is taken from the resolved
    principal and never from the form, so a comment cannot be signed by someone
    else — the store keeps an author on every entry.
    """
    if state.state != READY:
        return CommentResult(ok=False, error="This project's board is not open.")

    card_id = str(form.get("card_id") or "").strip()
    known = {str(row.get("id")) for row in state.rows}
    if not card_id or card_id not in known:
        return CommentResult(ok=False, error="That card is not on this board.")

    text = str(form.get("text") or "").strip()
    if not text:
        return CommentResult(ok=False, error="A comment needs something in it.")
    if len(text) > COMMENT_MAX:
        return CommentResult(ok=False, error=f"A comment can be up to {COMMENT_MAX} characters.")

    writer = comment or _default_comment
    try:
        writer(store=store, task_id=card_id, text=text, by=state.principal)
    except Exception as exc:  # noqa: BLE001 - one answer for every write failure
        logger.warning("[scitex-cards] project board comment failed: %s", exc, exc_info=True)
        return CommentResult(ok=False, error="The comment could not be saved. Try again.")
    return CommentResult(ok=True, card_id=card_id)


def _default_comment(**fields: Any) -> dict:
    """Append through the store's own comment API — the audit trail is the point."""
    from ..._store import comment_task

    return comment_task(**fields)


def _default_comments_loader(request: Any, card_id: str) -> list[dict]:
    """ONE card's comments, bounded, from the child table the index covers."""
    from .._request_store import read_store
    from ..._project_board_query import comments_for

    return comments_for(card_id, store=read_store(request))


def group_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Rows grouped into ``[(status, rows), ...]`` in canonical order.

    Statuses with no rows are DROPPED rather than rendered empty: on a project
    board the useful question is "what is in flight", and six empty columns
    around one populated one pushes that answer off a phone screen. The count of
    everything is carried separately by the board's total.
    """
    grouped = []
    for status in canonical_statuses():
        in_status = [row for row in rows if str(row.get("status") or "") == status]
        if in_status:
            grouped.append({"status": status, "rows": in_status, "count": len(in_status)})
    leftover = [
        row for row in rows if str(row.get("status") or "") not in canonical_statuses()
    ]
    if leftover:
        grouped.append({"status": "", "rows": leftover, "count": len(leftover)})
    return grouped


#: HTTP answers per state, chosen for the caller rather than for the page.
#:
#: denied -> 403: the request is understood and refused. It is NOT the 403 the
#:   repo warns about elsewhere — that warning is about "this deployment has no
#:   store", which must not share a code with Cloudflare Access' own 403 for a
#:   DIFFERENT reason. Here the refusal is about this project and this viewer.
#: unavailable -> 503: retryable, and the retry control is the point.
#: everything else -> 200: no-project, empty and filtered-empty are successful
#:   answers to a well-formed request, and a 404 there would send a reader
#:   hunting for a bug in their URL instead of creating their first card.
STATUS_FOR_STATE = {
    DENIED: 403,
    UNAVAILABLE: 503,
}


def _version() -> str:
    """The leaf's own version, for the header band.

    Read here rather than imported from a shared reader because develop has no
    shared reader yet: the board and DM pages each do
    ``from scitex_cards import __version__`` inline. #1024 consolidates those
    three copies into one ``_cards_version()``; when it lands, this call is the
    fourth caller to migrate rather than a fourth implementation to find.
    """
    from scitex_cards import __version__

    return __version__


def render_project_card(
    request: Any,
    state: BoardState,
    card_id: str,
    *,
    edit_error: str = "",
    comment_error: str = "",
    comments: Sequence[Mapping[str, Any]] = (),
):
    """Render ONE card's page, or the reason it cannot be shown.

    The card is looked up in ``state.rows`` — the rows the page already resolved
    through the SQL tenancy predicate — so the detail view inherits exactly the
    same boundary as the board, with no second query and no second rule. An id
    that is not in that set gets a 404 and one sentence, identical whether the card
    belongs to another tenant, another project or nothing at all: a detail route
    that distinguished those would be a better oracle than the board is.
    """
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    from ..views import _BOARD_ALIASES, _cards_shell_context, _include_root

    api_base = _card_page_api_base(request, card_id)
    card = next((row for row in state.rows if str(row.get("id")) == card_id), None)

    if card is None or state.state != READY:
        # ONE answer for "no such card", "not yours" and "no board here": the
        # reader is told what happened, and told nothing about what else exists.
        context = {
            **_cards_shell_context(request, api_base),
            "api_base": api_base,
            "scitex_cards_version": _version(),
            "board": state,
            "card_id": card_id,
            "card": None,
            "edit_error": "",
        }
        html = render_to_string("scitex_cards/project_card.html", context, request=request)
        status = STATUS_FOR_STATE.get(state.state, 404)
        return HttpResponse(html, status=status if status != 200 else 404)

    context = {
        **_cards_shell_context(request, api_base),
        "api_base": api_base,
        "scitex_cards_version": _version(),
        "board": state,
        "card": card,
        "card_id": card_id,
        "statuses": canonical_statuses(),
        "blockers": canonical_blockers(),
        "edit_error": edit_error,
        "comment_error": comment_error,
        "comments": comments,
        "comment_max": COMMENT_MAX,
        "note_max": NOTE_MAX,
        "title_max": CREATE_TITLE_MAX,
    }
    html = render_to_string("scitex_cards/project_card.html", context, request=request)
    status = 400 if (edit_error or comment_error) else 200
    return HttpResponse(html, status=status)


def _card_page_api_base(request: Any, card_id: str) -> str:
    """The include root for a page whose own path ENDS with a card id.

    ``_include_root`` strips a known alias segment off the END of the path, which
    is right for ``/projects`` and wrong for ``/projects/<id>``: the last segment
    there is the card, not the alias, so the function would hand back the card's own
    URL as the mount root and every link on the page would nest one level deeper
    per click. So the card segment is removed FIRST, and the same alias strip then
    runs on the remainder — at a sub-path mount too, where ``/apps/cards/projects/
    <id>`` must yield ``/apps/cards`` and not ``/apps/cards/projects/<id>``.
    """
    from ..views import _BOARD_ALIASES, _include_root

    path = str(getattr(request, "path", "") or "")
    for suffix in (f"/{card_id}/", f"/{card_id}"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] or "/"
            break
    return _include_root(path, ("projects",) + tuple(_BOARD_ALIASES))


def project_card_page(request, card_id: str):
    """One card: its content, and the editor for the two fields that need room."""
    from django.http import HttpResponseRedirect

    from .._request_store import read_store

    state = board_state(request)
    if getattr(request, "method", "GET").upper() != "POST":
        try:
            comments = list(_default_comments_loader(request, card_id))
        except Exception as exc:  # noqa: BLE001 - the card is worth showing without them
            logger.warning("[scitex-cards] project board: comments unavailable: %s", exc)
            comments = []
        return render_project_card(
            request,
            state,
            card_id,
            comments=comments,
            comment_error="" if comments is not None else "",
        )

    form = getattr(request, "POST", {}) or {}
    action = str(form.get("action") or "update").strip().lower()
    store = read_store(request)

    if action == "comment":
        written = comment_card(state, form, store=store)
        if not written.ok:
            return render_project_card(request, state, card_id, comment_error=written.error)
    else:
        changed = update_card(state, form, store=store)
        if not changed.ok:
            return render_project_card(request, state, card_id, edit_error=changed.error)

    api_base = _card_page_api_base(request, card_id)
    target = f"{api_base}/projects/{card_id}?project={state.project}&updated={card_id}"
    return HttpResponseRedirect(target)


def render_project_board(
    request: Any,
    state: BoardState,
    *,
    host_picker_available: Optional[bool] = None,
    create_error: str = "",
    created_id: str = "",
    updated_id: str = "",
    create_status: int = 200,
):
    """Render ``state`` as this page's HTTP response.

    Split from :func:`project_board_page` so the HTTP CONTRACT — which state is a
    403, which is a 503, which is a 200 — is a function of the state rather than
    of a request that had to reach a store first. The view below is then three
    lines and has nothing left to test that this does not.
    """
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    from ..views import _BOARD_ALIASES, _cards_shell_context, _include_root

    # The aliases this page can be reached by, longest first, so the include
    # root the shell's mount contract needs is recovered the same way the fleet
    # board does it — see ``_include_root`` for why the strip is segment-anchored.
    aliases = ("projects",) + tuple(_BOARD_ALIASES)
    api_base = _include_root(request.path, aliases)

    context = {
        **_cards_shell_context(request, api_base),
        "api_base": api_base,
        "scitex_cards_version": _version(),
        "board": state,
        "groups": group_rows(state.rows),
        "statuses": canonical_statuses(),
        "blockers": canonical_blockers(),
        "create_default_status": CREATE_DEFAULT_STATUS,
        "create_title_max": CREATE_TITLE_MAX,
        "archive_status": ARCHIVE_STATUS,
        "create_error": create_error,
        "created_id": created_id,
        "updated_id": updated_id,
        "host_picker_available": (
            _host_picker_available() if host_picker_available is None else host_picker_available
        ),
    }
    html = render_to_string("scitex_cards/project_board.html", context, request=request)
    status = STATUS_FOR_STATE.get(state.state, 200)
    if create_error:
        # A refused create is a 400, not a board: the reader's input was wrong and
        # they can fix it, which is different from every state above.
        status = create_status if create_status != 200 else 400
    return HttpResponse(html, status=status)


def project_board_page(request):
    """Serve the project-scoped board, and take its Create submission.

    CREATE IS POST-REDIRECT-GET, deliberately: a browser refresh after a create
    must not file a second card, and the redirect target is derived from the
    RESOLVED state rather than from the POST body, so the one thing the client
    can influence is the title it typed.
    """
    from django.http import HttpResponseRedirect

    state = board_state(request)
    if getattr(request, "method", "GET").upper() != "POST":
        query = getattr(request, "GET", None) or {}
        marker = str(query.get("created") or query.get("updated") or "").strip()
        return render_project_board(
            request,
            state,
            created_id=marker if query.get("created") else "",
            updated_id=marker if query.get("updated") else "",
        )

    from .._request_store import read_store
    from ..views import _BOARD_ALIASES, _include_root

    form = getattr(request, "POST", {}) or {}
    action = str(form.get("action") or "create").strip().lower()
    store = read_store(request)

    if action == "update":
        changed = update_card(state, form, store=store)
        if not changed.ok:
            return render_project_board(request, state, create_error=changed.error)
        marker = f"updated={changed.card_id}"
    elif action == "create":
        created = create_card(state, form, store=store)
        if not created.ok:
            return render_project_board(request, state, create_error=created.error)
        marker = f"created={created.card_id}"
    else:
        # An unknown action is refused rather than treated as the default: a form
        # field naming a verb this page does not have is a caller error, and
        # guessing which verb it meant is how a board starts writing things nobody
        # asked for.
        return render_project_board(
            request,
            state,
            create_error=f"Unknown action {action!r}.",
        )

    api_base = _include_root(request.path, ("projects",) + tuple(_BOARD_ALIASES))
    target = f"{api_base}/projects?project={state.project}&{marker}"
    return HttpResponseRedirect(target)


def _host_picker_available() -> bool:
    """True when a HOST has registered its project provider with the SDK.

    The template renders ONE picker: the SDK's when the host registered one,
    otherwise the standalone form over this viewer's own authorized projects.
    Asking here rather than guessing in the template keeps the decision in one
    place and keeps a missing scitex-ui API a graceful degradation (the same
    rule ``settings.py`` applies to the element inspector) instead of a
    TemplateSyntaxError on a deployment whose SDK predates project scope.
    """
    try:
        from scitex_ui.project_scope import host_project_provider_url
    except ImportError:  # older scitex-ui: no provider API, so no host picker
        return False
    return bool(host_project_provider_url())
