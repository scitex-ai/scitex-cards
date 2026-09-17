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
    filters: dict = field(default_factory=dict)
    detail: str = ""

    @property
    def filters_active(self) -> bool:
        return any(bool(v) for v in self.filters.values())


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


def _default_loader(request: Any) -> Sequence[dict]:
    """Rows for this request, through the board's own store reader.

    Reused rather than re-implemented so the project board and the fleet board
    can never disagree about which store they are looking at, and so a store
    that cannot be read raises HERE where the state machine already maps every
    load failure onto one honest answer. ``allow_stale`` matches the other
    read-only page payloads: a page behind one refresh cycle is invisible, the
    wait for a full store rebuild is not. Imported lazily because ``views``
    imports the handler package this module belongs to.
    """
    from ..views import _get_board

    return list(_get_board(request, allow_stale=True).tasks)


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


def board_state(
    request: Any,
    *,
    loader: Optional[Callable[[Any], Sequence[dict]]] = None,
    provider: Optional[Any] = None,
) -> BoardState:
    """Decide which of the six states this request is in, and with what rows.

    ``loader`` is injectable so the tenancy and the state machine are testable
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

    load = loader or _default_loader
    try:
        rows = list(load(request))
    except Exception as exc:  # noqa: BLE001 - every load failure is ONE state to the reader
        logger.warning("[scitex-cards] project board: store unavailable: %s", exc, exc_info=True)
        return BoardState(
            state=UNAVAILABLE,
            principal=principal,
            is_staff=is_staff,
            filters=filters,
            detail=str(exc)[:200],
        )

    # TENANCY FIRST. A viewer the boundary cannot name sees nothing at all — not
    # an empty project list, which would still confirm the store has projects.
    if not is_staff and not principal:
        return BoardState(state=DENIED, principal=principal, is_staff=is_staff, filters=filters)

    authorized = authorized_rows(rows, principal, is_staff=is_staff)
    projects = projects_of(authorized)
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

    project_rows = rows_of_project(authorized, current)
    if not project_rows:
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
            total=len(project_rows),
            filters=filters,
        )

    return BoardState(
        state=READY,
        principal=principal,
        is_staff=is_staff,
        project=current,
        projects=projects,
        rows=tuple(visible),
        total=len(project_rows),
        filters=filters,
    )


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


def render_project_board(
    request: Any,
    state: BoardState,
    *,
    host_picker_available: Optional[bool] = None,
    create_error: str = "",
    created_id: str = "",
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
        "create_default_status": CREATE_DEFAULT_STATUS,
        "create_title_max": CREATE_TITLE_MAX,
        "create_error": create_error,
        "created_id": created_id,
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
        created = str((getattr(request, "GET", None) or {}).get("created") or "").strip()
        return render_project_board(request, state, created_id=created)

    from .._request_store import read_store
    from ..views import _BOARD_ALIASES, _include_root

    result = create_card(state, getattr(request, "POST", {}), store=read_store(request))
    if not result.ok:
        return render_project_board(request, state, create_error=result.error)

    api_base = _include_root(request.path, ("projects",) + tuple(_BOARD_ALIASES))
    target = f"{api_base}/projects?project={state.project}&created={result.card_id}"
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
