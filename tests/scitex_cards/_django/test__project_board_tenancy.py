#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PROJECT-SCOPED board: tenant isolation (A/B) and the six states.

WHY THESE TESTS ARE SHAPED THIS WAY. The card's acceptance is "server-side
tenant isolation; ordinary users never see fleet/global cards", and the failure
it guards against is the P0 that produced ``_user_row_scope``: a read path that
returns every row to whoever is authenticated. So the tests below are A/B tests —
the SAME request shape, two viewers, and the assertion is about what is ABSENT
from one of them, never merely about what is present.

They are hermetic on purpose (an injected loader, no database): the predicate and
the state machine are pure functions of rows, so a test that needs Postgres to
prove "alice cannot see bob's project" is testing the database rather than the
boundary. The route tests at the bottom call the view directly through
``RequestFactory`` rather than the test ``Client``, which is how every other page
test in this repo works and keeps the app's loopback-only ``ALLOWED_HOSTS`` out
of what these tests are about.
"""

from __future__ import annotations

import pytest

from scitex_cards._django.handlers import project_board as pb


class _User:
    """The minimum Django user this page reads: a name, staff, authenticated."""

    def __init__(self, name: str, *, is_staff: bool = False, authenticated: bool = True):
        self._name = name
        self.is_staff = is_staff
        self.is_authenticated = authenticated

    def get_username(self) -> str:
        return self._name


class _Request:
    """A request stub: exactly the four things ``board_state`` may touch."""

    def __init__(self, user=None, params=None, session=None):
        self.user = user
        self.GET = params or {}
        self.session = session if session is not None else {}


class _Provider:
    """A host-style provider: accessibility decided OUTSIDE the store."""

    def __init__(self, project_ids):
        self._ids = tuple(project_ids)
        self.remembered = []

    def list_projects(self, request=None):
        from scitex_ui.project_scope import ProjectEntry

        return [ProjectEntry(id=pid, name=pid) for pid in self._ids]

    def last_visited(self, request=None):
        return None

    def remember(self, request, project_id):
        self.remembered.append(project_id)


def _row(card_id, *, owner, project, status="in_progress", title=None, assignee=None):
    return {
        "id": card_id,
        "title": title or card_id.replace("-", " ").title(),
        "status": status,
        "project": project,
        "assignee": assignee if assignee is not None else owner,
        "created_by": owner,
        "agent": owner,
        "priority": 2,
    }


#: Alice and Bob each own one card in their OWN project, and — the case that
#: matters — Alice's project ALSO holds a card she does not own. A page that
#: filtered by project alone would show it to her; the composed filter must not.
_ALICE = _row("alice-a", owner="alice", project="proj-alpha")
_ALICE_FOREIGN = _row("dana-in-alpha", owner="dana", project="proj-alpha")
_BOB = _row("bob-b", owner="bob", project="proj-beta", status="blocked")
_FLEET = _row("fleet-global", owner="operator", project="")


def _loader(rows):
    return lambda request: rows


def _sources(rows, viewer, *, is_staff=False):
    """The two loaders the state machine asks for, built from one fixture list.

    TENANCY LIVES IN THE LOADER NOW, and the fakes model that rather than
    filtering afterwards: in production the predicate is in the SQL ``WHERE``
    (``_project_board_query``, built from ``OWNED_FIELDS``), so a fake that
    handed back every row would be testing a loader this page never uses. The
    state machine still re-applies the predicate as defence in depth.

    The two questions stay separate — the viewer's project list, then one
    project's rows — because that is what the page now asks, and the shape that
    asked once for everything measured 72.7s cold on the shared store.
    """
    authorized = pb.authorized_rows(rows, viewer, is_staff=is_staff)

    def _projects(request):
        return pb.projects_of(authorized)

    def _rows(request, project, offset=0):
        # LIMIT + OFFSET, like the SQL: the fake must not hand back more than a
        # page, or a test could pass while the real query is unbounded.
        page = pb.rows_of_project(authorized, project)[offset : offset + pb.DEFAULT_ROW_LIMIT]
        return page

    def _count(request, project):
        return len(pb.rows_of_project(authorized, project))

    return {
        "projects_loader": _projects,
        "rows_loader": _rows,
        "count_loader": _count,
    }


def _state(viewer, rows, params=None, *, is_staff=False, provider=None, session=None):
    request = _Request(_User(viewer, is_staff=is_staff), params, session)
    return pb.board_state(request, provider=provider, **_sources(rows, viewer, is_staff=is_staff))


# --- A/B tenant isolation ---------------------------------------------------


def test_alice_does_not_see_bobs_project_in_her_picker():
    """The picker is built from AUTHORIZED rows, so another tenant is absent."""
    # Arrange
    rows = [_ALICE, _ALICE_FOREIGN, _BOB, _FLEET]
    # Act
    state = _state("alice", rows)
    # Assert
    assert "proj-beta" not in state.projects


def test_alice_sees_no_card_owned_by_someone_else_in_her_own_project():
    """Project tenancy composes with the row predicate; it does not replace it."""
    # Arrange
    rows = [_ALICE, _ALICE_FOREIGN, _BOB, _FLEET]
    params = {"project": "proj-alpha"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert [row["id"] for row in state.rows] == ["alice-a"]


def test_alice_is_denied_when_she_names_bobs_project():
    """An inaccessible explicit project is a REFUSAL, not an empty board."""
    # Arrange
    rows = [_ALICE, _BOB]
    params = {"project": "proj-beta"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert state.state == pb.DENIED


def test_a_denied_project_never_falls_back_to_the_last_visited_one():
    """scitex-ui resolves an inaccessible explicit project to None — and the
    page must not quietly show a DIFFERENT project instead, which would read as
    "here is your board" while the reader asked for someone else's."""
    # Arrange
    rows = [_ALICE, _BOB]
    params = {"project": "proj-beta"}
    session = {pb.SESSION_KEY: "proj-alpha"}  # a project alice CAN open
    # Act
    state = _state("alice", rows, params, session=session)
    # Assert
    assert state.rows == ()


def test_staff_sees_the_whole_set_including_other_tenants():
    """The control: the staff bypass still exists, for the operator's board."""
    # Arrange
    rows = [_ALICE, _BOB]
    params = {"project": "proj-beta"}
    # Act
    state = _state("operator", rows, params, is_staff=True)
    # Assert
    assert [row["id"] for row in state.rows] == ["bob-b"]


def test_a_viewer_that_cannot_be_named_sees_nothing():
    """Fail closed: an empty principal sees nothing at all — not an empty
    project list either, which would still confirm the store has projects.

    Asserted on ``authorized_rows`` because that is where the rule lives, and
    ``board_state`` is a call to this plus project selection: a test that
    reached it through a forged principal would be testing the forging.
    """
    # Arrange
    rows = [_ALICE, _BOB]
    # Act
    visible = pb.authorized_rows(rows, "")
    # Assert
    assert visible == []


def test_staff_still_bypass_the_fail_closed_rule():
    """The operator's board must not be emptied by the tenancy above it."""
    # Arrange
    rows = [_ALICE, _BOB]
    # Act
    visible = pb.authorized_rows(rows, "", is_staff=True)
    # Assert
    assert len(visible) == 2


def test_a_card_with_no_project_is_fleet_only():
    """A row whose project is empty is never part of a project board."""
    # Arrange
    rows = [_FLEET]
    params = {"project": "proj-alpha"}
    # Act
    state = _state("operator", rows, params)
    # Assert
    assert state.state == pb.DENIED


# --- the six states ---------------------------------------------------------


def test_no_project_state_when_nothing_is_selected():
    """No explicit and no stored project: the page asks instead of guessing."""
    # Arrange
    rows = [_ALICE, _BOB]
    # Act
    state = _state("alice", rows)
    # Assert
    assert state.state == pb.NO_PROJECT


def test_empty_state_when_the_project_is_accessible_and_has_no_cards():
    """A host-provided project with no rows yet is EMPTY, not NO_PROJECT."""
    # Arrange
    params = {"project": "proj-fresh"}
    provider = _Provider(["proj-fresh"])
    # Act
    state = _state("alice", [], params, provider=provider)
    # Assert
    assert state.state == pb.EMPTY


def test_filtered_empty_is_distinct_from_empty():
    """Filters that exclude everything must not be reported as an empty board."""
    # Arrange
    rows = [_ALICE]
    params = {"project": "proj-alpha", "q": "nothing-matches"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert state.state == pb.FILTERED_EMPTY


def test_filtered_empty_still_reports_the_projects_real_total():
    """The reader is told the board is not empty, only the filter result is."""
    # Arrange
    rows = [_ALICE, _row("alice-c", owner="alice", project="proj-alpha")]
    params = {"project": "proj-alpha", "status": "done"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert state.total == 2


def test_unavailable_state_when_the_store_cannot_be_read():
    """One honest answer for every load failure, and it is retryable."""

    # Arrange
    def _boom(request):
        raise RuntimeError("connection to server failed")

    # Act
    state = pb.board_state(_Request(_User("alice")), projects_loader=_boom)
    # Assert
    assert state.state == pb.UNAVAILABLE


def test_unavailable_also_when_only_the_second_query_fails():
    """The project list answering is not the page working: a failure while
    loading the cards is the same retryable answer, not a half-rendered board."""
    # Arrange
    def _boom(request, selected):
        raise RuntimeError("connection to server failed")

    rows = [_ALICE]
    # Act
    state = pb.board_state(
        _Request(_User("alice"), {"project": "proj-alpha"}),
        projects_loader=lambda request: pb.projects_of(rows),
        rows_loader=_boom,
    )
    # Assert
    assert state.state == pb.UNAVAILABLE


def test_denied_is_403_and_unavailable_is_503():
    """The HTTP contract, asserted in one place so a new state cannot drift."""
    # Arrange
    expected = {pb.DENIED: 403, pb.UNAVAILABLE: 503}
    # Act
    codes = pb.STATUS_FOR_STATE
    # Assert
    assert codes == expected


# --- filters ----------------------------------------------------------------


def test_status_filter_is_applied_server_side():
    """Filtering happens before rendering, so unselected rows never leave."""
    # Arrange
    rows = [_ALICE, _row("alice-done", owner="alice", project="proj-alpha", status="done")]
    params = {"project": "proj-alpha", "status": "done"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert [row["status"] for row in state.rows] == ["done"]


def test_assignee_filter_is_applied_server_side():
    """Assignee is an exact match, not a substring: ids are not prose."""
    # Arrange
    rows = [_ALICE, _row("alice-e", owner="alice", project="proj-alpha", assignee="alice-2")]
    params = {"project": "proj-alpha", "assignee": "alice"}
    # Act
    state = _state("alice", rows, params)
    # Assert
    assert [row["id"] for row in state.rows] == ["alice-a"]


def test_search_matches_a_title_substring_case_insensitively():
    """A search box that is case-sensitive surprises every reader."""
    # Arrange
    row = _row("alice-f", owner="alice", project="proj-alpha", title="Fix the Graph Layout")
    params = {"project": "proj-alpha", "q": "graph layout"}
    # Act
    state = _state("alice", [row], params)
    # Assert
    assert [r["id"] for r in state.rows] == ["alice-f"]


def test_group_rows_keeps_canonical_order_and_drops_empty_statuses():
    """Empty columns would push the real work off a phone screen."""
    # Arrange
    rows = [
        _row("alice-g", owner="alice", project="proj-alpha", status="blocked"),
        _row("alice-h", owner="alice", project="proj-alpha", status="in_progress"),
    ]
    # Act
    groups = pb.group_rows(rows)
    # Assert
    assert [group["status"] for group in groups] == ["in_progress", "blocked"]


def test_group_rows_carries_unknown_statuses_rather_than_hiding_them():
    """A row with a status the vocabulary does not know must still be visible."""
    # Arrange
    rows = [dict(_ALICE, status="brand-new")]
    # Act
    groups = pb.group_rows(rows)
    # Assert
    assert (groups[-1]["status"], groups[-1]["count"]) == ("", 1)


def test_the_module_does_not_restate_the_canonical_status_vocabulary():
    """Columns come from the store's own VALID_STATUSES, not a second list."""
    # Arrange
    from scitex_cards._store import VALID_STATUSES

    expected = tuple(VALID_STATUSES)
    # Act
    asked_for = pb.canonical_statuses()
    # Assert
    assert asked_for == expected


# --- the route ---------------------------------------------------------------


def _render(state, *, path="/projects", user="alice", host_picker_available=None,
            create_error="", created_id=""):
    """Render a state through the REAL page view, no patching.

    ``render_project_board`` is the production function the view delegates to;
    calling it with a constructed state is how a test asks "what does the page
    answer in THIS state" without a store, a request cycle or a mock.
    """
    from django.test import RequestFactory

    request = RequestFactory().get(path, HTTP_HOST="127.0.0.1")
    request.user = _User(user) if user else None
    return pb.render_project_board(
        request,
        state,
        host_picker_available=host_picker_available,
        create_error=create_error,
        created_id=created_id,
    )


def _state_only(status, **kwargs):
    return pb.BoardState(state=status, principal="alice", is_staff=False, **kwargs)


def test_the_page_renders_the_state_it_was_given():
    """The page's own state marker is what a browser check asserts on."""
    # Arrange
    state = _state_only(pb.NO_PROJECT)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert 'data-stx-state="no-project"' in body


def test_the_page_answers_403_for_a_denied_project():
    """Refusal is a status code, so a client cannot mistake it for a board."""
    # Arrange
    state = _state_only(pb.DENIED, project="proj-beta")
    # Act
    response = _render(state, path="/projects?project=proj-beta")
    # Assert
    assert response.status_code == 403


def test_the_page_answers_503_when_the_store_is_unavailable():
    """Retryable, and distinct from the refusal above."""
    # Arrange
    state = _state_only(pb.UNAVAILABLE)
    # Act
    response = _render(state)
    # Assert
    assert response.status_code == 503


@pytest.mark.parametrize("status", [pb.READY, pb.EMPTY, pb.FILTERED_EMPTY, pb.NO_PROJECT])
def test_non_denied_states_render_the_page_instead_of_an_error(status):
    """Success states share one HTTP code and differ by their marker."""
    # Arrange
    state = _state_only(status)
    # Act
    response = _render(state)
    # Assert
    assert response.status_code == 200


def test_the_unavailable_page_offers_a_retry():
    """A retryable failure with no way to retry is just a failure."""
    # Arrange
    state = _state_only(pb.UNAVAILABLE)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert "data-stx-retry" in body


def test_every_state_the_page_can_be_in_is_classified():
    """A new state must be DECLARED as a success state, not silently answer 200."""
    # Arrange
    states = {pb.NO_PROJECT, pb.DENIED, pb.UNAVAILABLE, pb.EMPTY, pb.FILTERED_EMPTY, pb.READY}
    success = {pb.READY, pb.EMPTY, pb.FILTERED_EMPTY, pb.NO_PROJECT}
    # Act
    unmapped = {status for status in states if status not in pb.STATUS_FOR_STATE}
    # Assert
    assert unmapped == success


def test_standalone_mount_offers_a_picker_over_the_viewers_own_projects():
    """No host provider: the page falls back to one form, not to a dead end."""
    # Arrange
    state = _state_only(pb.NO_PROJECT, projects=("proj-alpha",))
    # Act
    body = _render(state, host_picker_available=False).content.decode()
    # Assert
    assert "data-stx-picker-select" in body


def test_host_provider_means_the_sdk_picker_and_no_second_one():
    """The SDK owns the canonical selector; the leaf must not ship a twin.

    The provider is registered through Django's own ``override_settings`` — the
    real mechanism a host uses (``SCITEX_PROJECT_PROVIDER_URL``), read by the SDK
    at render time — rather than by patching the SDK's function reference.
    """
    # Arrange
    from django.test import override_settings

    state = _state_only(pb.NO_PROJECT, projects=("proj-alpha",))
    # Act
    with override_settings(SCITEX_PROJECT_PROVIDER_URL="/api_project_scope"):
        body = _render(state, host_picker_available=True).content.decode()
    # Assert
    assert ("data-stx-project-picker" in body, "data-stx-picker-select" in body) == (True, False)


def test_the_page_is_reachable_by_both_spellings():
    """A published URL is a migration, not a label: /projects and /projects/
    both answer, because a trailing slash is the natural thing to type."""
    # Arrange
    from django.urls import reverse

    # Act
    both = (reverse("project_board"), reverse("project_board_slash"))
    # Assert
    assert both == ("/projects", "/projects/")


# --- create (slice 2a) -------------------------------------------------------
#
# EVERY test below writes to a REAL store handed out by the harness (`new_store`
# gives a throwaway Postgres store per call) and reads the row back. No test
# double for the writer, because the claims worth making here are about what
# LANDED: that the project came from the resolved state rather than the form,
# and that the creator came from the principal rather than the form. A stub
# would let those pass while the real call sent something else.


def _ready(project="proj-alpha", principal="alice"):
    return pb.BoardState(state=pb.READY, principal=principal, is_staff=False, project=project)


class _Recorder:
    """A hand-written stand-in for the store's writer, for the cases that are
    about WHAT WAS SENT rather than about what a store accepted.

    Not a mock library and not a monkeypatch: it is a callable handed in through
    the same ``add=`` argument the production default arrives through, and it
    records the payload so the forcing claims can be asserted on a machine with
    no writable PostgreSQL. The store-backed tests below assert the same claims
    end to end for the environments that have one.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, **fields):
        self.calls.append(fields)
        return {"id": fields.get("id")}


def test_the_writer_is_handed_the_states_project_not_the_forms():
    """Hermetic twin of the store-backed case, for a machine with no scratch PG."""
    # Arrange
    recorder = _Recorder()
    form = {"title": "Ship the slice", "project": "proj-beta"}
    # Act
    pb.create_card(_ready(), form, add=recorder, now="20260917070006")
    # Assert
    assert recorder.calls[0]["project"] == "proj-alpha"


def test_the_writer_is_handed_the_principal_not_the_forms_author():
    """The created_by and agent fields both come from the resolved principal."""
    # Arrange
    recorder = _Recorder()
    form = {"title": "Ship the slice", "created_by": "dana", "agent": "dana"}
    # Act
    pb.create_card(_ready(), form, add=recorder, now="20260917070007")
    # Assert
    assert (recorder.calls[0]["created_by"], recorder.calls[0]["agent"]) == ("alice", "alice")


def test_the_writer_gets_a_whole_number_priority_or_none():
    """Priority reaches the store as an int, because the store validates an int."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.create_card(_ready(), {"title": "Ship the slice", "priority": "3"},
                   add=recorder, now="20260917070008")
    # Assert
    assert recorder.calls[0]["priority"] == 3


def test_the_generated_id_reads_like_a_card_id():
    """Ids on this board are readable; a page-created card joins that, and the
    stamp+suffix keeps two cards with the same title distinct."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.create_card(_ready(), {"title": "Fix the Graph Layout!"},
                   add=recorder, now="20260917070909")
    # Assert
    assert recorder.calls[0]["id"].startswith("fix-the-graph-layout-20260917070909-")


def _rows(dsn, card_id):
    from scitex_cards import _store

    return [row for row in _store.list_tasks(store=dsn) if row["id"] == card_id]


def test_create_writes_a_card_into_the_current_project(new_store):
    """The happy path, against a real store, read back by id."""
    # Arrange
    dsn = new_store()
    # Act
    result = pb.create_card(_ready(), {"title": "Ship the slice"}, store=dsn, now="20260917070000")
    # Assert
    assert [row["project"] for row in _rows(dsn, result.card_id)] == ["proj-alpha"]


def test_create_signs_the_card_with_the_principal_not_the_form(new_store):
    """A crafted POST cannot file a card under someone else's name."""
    # Arrange
    dsn = new_store()
    form = {"title": "Ship the slice", "created_by": "dana", "agent": "dana"}
    # Act
    result = pb.create_card(_ready(), form, store=dsn, now="20260917070001")
    # Assert
    assert [row["created_by"] for row in _rows(dsn, result.card_id)] == ["alice"]


def test_create_cannot_file_into_another_tenants_project(new_store):
    """A crafted POST cannot choose a project: the state's wins."""
    # Arrange
    dsn = new_store()
    form = {"title": "Ship the slice", "project": "proj-beta"}
    # Act
    result = pb.create_card(_ready(), form, store=dsn, now="20260917070002")
    # Assert
    assert [row["project"] for row in _rows(dsn, result.card_id)] == ["proj-alpha"]


def test_create_defaults_the_status_to_in_flight(new_store):
    """A card typed into a board is in flight; 'deferred' is the store's own
    default and the wrong one for a person who just typed a title."""
    # Arrange
    dsn = new_store()
    # Act
    result = pb.create_card(_ready(), {"title": "Ship the slice"}, store=dsn, now="20260917070003")
    # Assert
    assert [row["status"] for row in _rows(dsn, result.card_id)] == [pb.CREATE_DEFAULT_STATUS]


def test_create_defaults_the_assignee_to_the_viewer(new_store):
    """An unassigned card on a personal board is a card nobody picks up."""
    # Arrange
    dsn = new_store()
    # Act
    result = pb.create_card(_ready(), {"title": "Ship the slice"}, store=dsn, now="20260917070004")
    # Assert
    assert [row["assignee"] for row in _rows(dsn, result.card_id)] == ["alice"]


def test_create_keeps_a_status_the_store_has(new_store):
    """An explicit status from the form is honoured when the vocabulary knows it.

    It now carries its GATE: this test used to write `blocked` with no blocker,
    which is the defect the blocker gate exists for — the store tolerated it with a
    warning, and after the gate landed the same call was refused (accepted item 5
    of card gui-project-board-blocker-gate-20260917). Writing the gate is the fix,
    and asserting the row carries it is the point of doing so.
    """
    # Arrange
    dsn = new_store()
    form = {"title": "Ship the slice", "status": "blocked", "blocker": "dependency"}
    # Act
    result = pb.create_card(_ready(), form, store=dsn, now="20260917070005")
    # Assert
    rows = _rows(dsn, result.card_id)
    assert [(row["status"], row["blocker"]) for row in rows] == [("blocked", "dependency")]


def test_create_refuses_a_status_the_store_does_not_have():
    """An invented status must be refused rather than written as a new column."""
    # Arrange
    form = {"title": "Ship the slice", "status": "banana"}
    # Act
    result = pb.create_card(_ready(), form)
    # Assert
    assert (result.ok, result.card_id) == (False, None)


def test_create_refuses_an_empty_title():
    """A card with no title is a row nobody can read in a graph."""
    # Arrange
    form = {"title": "   "}
    # Act
    result = pb.create_card(_ready(), form)
    # Assert
    assert result.ok is False


def test_create_refuses_a_title_past_the_page_limit():
    """The page limit is stated once and enforced on the write, not the input."""
    # Arrange
    form = {"title": "x" * (pb.CREATE_TITLE_MAX + 1)}
    # Act
    result = pb.create_card(_ready(), form)
    # Assert
    assert result.ok is False


def test_create_refuses_a_non_numeric_priority():
    """Priority is an integer field; 'high' must not reach the store."""
    # Arrange
    form = {"title": "Ship the slice", "priority": "high"}
    # Act
    result = pb.create_card(_ready(), form)
    # Assert
    assert result.ok is False


def test_create_refuses_when_there_is_no_project_to_file_into():
    """No project selected: there is nowhere for the card to go, and saying so
    is better than filing it in whichever project happens to be around."""
    # Arrange
    state = pb.BoardState(state=pb.NO_PROJECT, principal="alice", is_staff=False)
    # Act
    result = pb.create_card(state, {"title": "Orphan"})
    # Assert
    assert result.ok is False


def test_create_refuses_on_a_denied_project():
    """A refused project must not become a write target."""
    # Arrange
    state = pb.BoardState(state=pb.DENIED, principal="alice", is_staff=False)
    # Act
    result = pb.create_card(state, {"title": "Trespass"})
    # Assert
    assert result.ok is False


def test_a_refused_create_writes_nothing(new_store):
    """The refusal is a decision, not a write that was rolled back: the store
    must gain no row at all."""
    # Arrange
    from scitex_cards import _store

    dsn = new_store()
    before = len(_store.list_tasks(store=dsn))
    # Act
    pb.create_card(_ready(), {"title": ""}, store=dsn)
    # Assert
    assert len(_store.list_tasks(store=dsn)) == before


def test_the_create_form_carries_the_projects_url_and_the_page_limit():
    """The form POSTs back to this page with the project it belongs to, and
    says the same title limit the server enforces."""
    # Arrange
    state = _state_only(pb.READY, project="proj-alpha")
    # Act
    body = _render(state).content.decode()
    # Assert
    assert ('data-stx-create-title' in body, f'maxlength="{pb.CREATE_TITLE_MAX}"' in body) == (True, True)


def test_no_create_form_where_there_is_no_project_to_create_in():
    """A create form on a denied or project-less page would be a button that
    cannot work."""
    # Arrange
    state = _state_only(pb.DENIED)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert "data-stx-create-submit" not in body


def test_a_refused_create_answers_400_and_says_why():
    """The reader's input was wrong, which is a different answer from every
    state above — and it is on the page, not only in the status code."""
    # Arrange
    state = _state_only(pb.READY, project="proj-alpha")
    # Act
    response = _render(state, create_error="A card needs a title.")
    # Assert
    assert (response.status_code, "A card needs a title." in response.content.decode()) == (400, True)


# --- act on a card: status, assignee, archive (slice 2b) ---------------------


def _board(rows=None, project="proj-alpha", principal="alice"):
    return pb.BoardState(
        state=pb.READY, principal=principal, is_staff=False, project=project, rows=tuple(rows or [_ALICE])
    )


def test_update_refuses_a_card_that_is_not_on_this_board():
    """The card id is the one thing the client controls, so it is only ever a
    LOOKUP key into the rows this page already rendered. A card from another
    tenant (or another project, or nowhere) is simply not in that set."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "bob-b", "status": "done"}
    # Act
    result = pb.update_card(_board(), form, update=recorder)
    # Assert
    assert (result.ok, recorder.calls) == (False, [])


def test_update_hands_the_store_only_the_fields_a_board_may_change():
    """A crafted form naming fields OUTSIDE `UPDATABLE_FIELDS` must not reach the
    writer — project, creator and scope are not the board's to change. (title and
    note ARE in the set now, because the card page edits them; that is asserted
    separately rather than by widening this test.)"""
    # Arrange
    recorder = _Recorder()
    form = {
        "card_id": "alice-a",
        "status": "done",
        "project": "proj-beta",
        "created_by": "dana",
        "agent": "dana",
        "scope": "fleet",
    }
    # Act
    pb.update_card(_board(), form, update=recorder)
    # Assert
    assert sorted(k for k in recorder.calls[0] if k not in ("store", "task_id")) == ["status"]


def test_update_names_the_card_it_was_given_and_not_another():
    """task_id reaches the store as the id that was validated, not as a rewritten
    one."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.update_card(_board(), {"card_id": "alice-a", "status": "done"}, update=recorder)
    # Assert
    assert recorder.calls[0]["task_id"] == "alice-a"


def test_update_refuses_a_status_the_store_does_not_have():
    """Same vocabulary check as create: the board cannot invent a column."""
    # Arrange
    form = {"card_id": "alice-a", "status": "banana"}
    # Act
    result = pb.update_card(_board(), form)
    # Assert
    assert result.ok is False


def test_update_refuses_when_nothing_would_change():
    """An empty change is refused rather than written as a no-op revision bump —
    the store's revision counter is a compare-and-set signal, not noise."""
    # Arrange
    form = {"card_id": "alice-a"}
    # Act
    result = pb.update_card(_board(), form)
    # Assert
    assert result.ok is False


def test_update_refuses_a_non_numeric_priority():
    """Same rule as create: the field is an integer on the row."""
    # Arrange
    form = {"card_id": "alice-a", "priority": "high"}
    # Act
    result = pb.update_card(_board(), form)
    # Assert
    assert result.ok is False


def test_update_refuses_when_the_board_is_not_open():
    """No board means no card actions: a denied or unavailable page must not
    accept a change just because a form was posted to it."""
    # Arrange
    state = pb.BoardState(state=pb.DENIED, principal="alice", is_staff=False)
    # Act
    result = pb.update_card(state, {"card_id": "alice-a", "status": "done"})
    # Assert
    assert result.ok is False


def test_archive_is_a_status_change_to_the_terminal_state():
    """Nothing is deleted on this board; archiving is the store's own terminal
    status, so the row keeps its history and the board stops showing it."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "alice-a", "status": pb.ARCHIVE_STATUS}
    # Act
    pb.update_card(_board(), form, update=recorder)
    # Assert
    assert recorder.calls[0]["status"] == "cancelled"


def test_assignee_is_only_sent_when_it_was_actually_supplied():
    """A form with no assignee field must not blank the assignee — "absent" and
    "empty" are different statements about a card."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.update_card(_board(), {"card_id": "alice-a", "status": "done"}, update=recorder)
    # Assert
    assert "assignee" not in recorder.calls[0]


def test_the_card_forms_carry_the_card_id_and_the_card_action_hook():
    """The browser evidence and the docs pipeline locate the control by its
    stable hook, and the server needs the id it must validate."""
    # Arrange
    state = _board()
    # Act
    body = _render(state).content.decode()
    # Assert
    assert ('data-stx-card-action="alice-a"' in body, 'name="card_id" value="alice-a"' in body) == (True, True)


def test_the_archive_control_sends_the_terminal_status():
    """The archive button is its own form with the status baked in, so it cannot
    be confused with the Update button's select."""
    # Arrange
    state = _board()
    # Act
    body = _render(state).content.decode()
    # Assert
    assert f'<input type="hidden" name="status" value="{pb.ARCHIVE_STATUS}">' in body


def test_no_card_actions_on_a_page_with_no_board():
    """A denied page must not render controls that cannot work."""
    # Arrange
    state = _state_only(pb.DENIED)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert "data-stx-card-action" not in body


def test_the_view_dispatches_an_update_action_to_the_update_path():
    """The POST entry point routes by the form's action field, and refuses a verb
    it does not have instead of treating it as a create."""
    # Arrange
    from django.test import RequestFactory

    request = RequestFactory().post("/projects", {"action": "explode"}, HTTP_HOST="127.0.0.1")
    request.user = _User("alice")
    # Act
    response = pb.project_board_page(request)
    # Assert
    assert "Unknown action" in response.content.decode()


# --- one card: open it, and edit the two fields that need room (slice 2c) ----


def _card_page(card_id="alice-a", *, state=None, path="/projects/alice-a", edit_error="",
               comment_error="", comments=(), user="alice"):
    """Render the card page through the REAL view, no patching."""
    from django.test import RequestFactory

    request = RequestFactory().get(path, HTTP_HOST="127.0.0.1")
    request.user = _User(user) if user else None
    return pb.render_project_card(
        request,
        state or _board(),
        card_id,
        edit_error=edit_error,
        comment_error=comment_error,
        comments=comments,
    )


def test_the_card_page_shows_the_card_it_was_asked_for():
    """The title, the status and the id are on the page: it is the card, not a
    generic panel."""
    # Arrange
    card_id = "alice-a"
    # Act
    body = _card_page(card_id).content.decode()
    # Assert
    assert (f'data-stx-card-detail="{card_id}"' in body, "Alice A" in body) == (True, True)


def test_the_card_page_refuses_a_card_that_is_not_on_this_board():
    """Same boundary as the inline controls: the id is a lookup key into the rows
    the board rendered, never a permission."""
    # Arrange
    state = _board()
    # Act
    response = _card_page("bob-b", state=state)
    # Assert
    assert response.status_code == 404


def test_the_refusal_does_not_say_whether_the_card_exists_elsewhere():
    """One sentence for "not yours", "not here" and "does not exist" — a page that
    distinguished them would be a better oracle than the board it sits beside."""
    # Arrange
    state = _board()
    # Act
    body = _card_page("bob-b", state=state).content.decode()
    # Assert
    assert "not on this board" in body


def test_the_card_page_refuses_when_the_board_is_not_open():
    """A denied project has no cards to open, and the page must not accept a POST
    just because the URL looked right."""
    # Arrange
    state = pb.BoardState(state=pb.DENIED, principal="alice", is_staff=False)
    # Act
    response = _card_page("alice-a", state=state)
    # Assert
    assert response.status_code == 403


def test_the_card_page_offers_the_editor_with_the_current_values():
    """An editor pre-filled from the row is what makes "edit" a change rather than
    a rewrite."""
    # Arrange
    state = _board()
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert ('data-stx-edit-title' in body, 'value="Alice A"' in body) == (True, True)


def test_an_edit_refusal_answers_400_and_says_why():
    """The reader's input was wrong: a different answer from a refusal to show the
    card, and it is on the page rather than only in the status code."""
    # Arrange
    state = _board()
    # Act
    response = _card_page(state=state, edit_error="A note can be up to 4000 characters.")
    # Assert
    assert (response.status_code, "A note can be up to 4000 characters." in response.content.decode()) == (400, True)


def test_update_accepts_a_title_and_a_note():
    """The two fields the card page adds to the board's inline verbs."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "alice-a", "title": "Renamed", "note": "a paragraph"}
    # Act
    pb.update_card(_board(), form, update=recorder)
    # Assert
    assert (recorder.calls[0]["title"], recorder.calls[0]["note"]) == ("Renamed", "a paragraph")


def test_update_refuses_an_empty_title():
    """A card with no title is a row nobody can read in a graph."""
    # Arrange
    form = {"card_id": "alice-a", "title": "   "}
    # Act
    result = pb.update_card(_board(), form)
    # Assert
    assert result.ok is False


def test_update_refuses_a_note_past_the_page_limit():
    """A note is a paragraph; a paste accident must not reach the store."""
    # Arrange
    form = {"card_id": "alice-a", "note": "x" * (pb.NOTE_MAX + 1)}
    # Act
    result = pb.update_card(_board(), form)
    # Assert
    assert result.ok is False


def test_update_can_clear_a_note_on_purpose():
    """An empty note is sent, unlike an empty assignee: clearing a note is a thing
    people mean, while losing an assignee to a stray save is not."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.update_card(_board(), {"card_id": "alice-a", "note": ""}, update=recorder)
    # Assert
    assert recorder.calls[0]["note"] == ""


def test_the_board_links_each_card_to_its_own_page():
    """A card is a thing with an address: the board must offer the way in."""
    # Arrange
    state = _board()
    # Act
    body = _render(state).content.decode()
    # Assert
    assert 'data-stx-card-open="alice-a"' in body


def test_both_card_urls_are_registered():
    """The address of a card, in the two spellings people type."""
    # Arrange
    from django.urls import reverse

    # Act
    both = (reverse("project_card", args=["alice-a"]), reverse("project_card_slash", args=["alice-a"]))
    # Assert
    assert both == ("/projects/alice-a", "/projects/alice-a/")


# --- the mount-relative URL defect (found by a browser click, 2026-09-17) ----


def test_no_url_on_the_board_is_protocol_relative_at_a_root_mount():
    """`{{ api_base }}/projects` renders `//projects` when api_base is empty, and a
    browser reads a leading `//` as a HOST. Measured: clicking a card left the
    browser on chrome-error://chromewebdata/ without a request ever reaching this
    app. Every href and form action must therefore be root-absolute."""
    # Arrange
    state = _board()
    # Act
    body = _render(state).content.decode()
    # Assert
    assert ('action="//' in body, 'href="//' in body) == (False, False)


def test_no_url_on_the_card_page_is_protocol_relative_at_a_root_mount():
    """Same defect, same rule, on the card page's own links and edit form."""
    # Arrange
    state = _board()
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert ('action="//' in body, 'href="//' in body) == (False, False)


def test_the_board_urls_carry_the_mount_prefix_at_a_sub_path_mount():
    """Behind the hub the app lives at /apps/cards, and the links must go there —
    root-absolute is necessary but not sufficient."""
    # Arrange
    from django.test import RequestFactory

    request = RequestFactory().get("/apps/cards/projects", HTTP_HOST="127.0.0.1")
    request.user = _User("alice")
    # Act
    body = pb.render_project_board(request, _board(), host_picker_available=False).content.decode()
    # Assert
    assert f'action="/apps/cards/projects?project=' in body


def test_the_card_page_recovers_the_mount_root_from_a_card_path():
    """On /apps/cards/projects/<id> the last segment is the CARD, not an alias: the
    mount root must still be /apps/cards, or every link on the page nests one level
    deeper per click."""
    # Arrange
    from django.test import RequestFactory

    request = RequestFactory().get("/apps/cards/projects/alice-a", HTTP_HOST="127.0.0.1")
    request.user = _User("alice")
    # Act
    body = pb.render_project_card(request, _board(), "alice-a").content.decode()
    # Assert
    assert f'action="/apps/cards/projects/alice-a?project=' in body


# --- paging: a bounded page must not present itself as the whole board -------
#
# The page shows at most DEFAULT_ROW_LIMIT cards. Reporting the PAGE LENGTH as the
# total would render "500 of 500" for a project holding 1,200 cards — a bounded
# view presented as a complete one, which is the failure this section exists to
# prevent. The total therefore comes from a COUNT query, and the page says
# "showing X-Y of N".


def _paged_state(rows, params, viewer="alice"):
    request = _Request(_User(viewer), params, None)
    return pb.board_state(request, **_sources(rows, viewer))


def _alice_rows(n, project="proj-alpha"):
    return [_row(f"alice-{i:04d}", owner="alice", project=project) for i in range(n)]


def test_the_total_is_the_stores_count_not_the_page_length():
    """1,200 cards and a 500-row page must read as 1,200, not as 500."""
    # Arrange
    rows = _alice_rows(3)
    sources = _sources(rows, "alice")
    sources["count_loader"] = lambda request, project: 1200
    sources["rows_loader"] = lambda request, project, offset=0: rows
    # Act
    state = pb.board_state(_Request(_User("alice"), {"project": "proj-alpha"}), **sources)
    # Assert
    assert (state.total, len(state.rows), state.has_next) == (1200, 3, True)


def test_a_full_page_with_more_to_come_says_so():
    """A page of exactly the limit with a larger count has a next page."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(pb.DEFAULT_ROW_LIMIT)), total=pb.DEFAULT_ROW_LIMIT * 2,
                          offset=0, page_size=pb.DEFAULT_ROW_LIMIT)
    # Act
    more = state.has_next
    # Assert
    assert more is True


def test_the_last_page_does_not_offer_a_next_one():
    """has_next is arithmetic over the count, so the end of the board is the end."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(10)), total=10, offset=0, page_size=10)
    # Act
    more = state.has_next
    # Assert
    assert more is False


def test_the_first_page_does_not_offer_a_previous_one():
    """A Previous link on the first page is a control that cannot work."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(10)), total=10, offset=0, page_size=10)
    # Act
    previous = state.has_prev
    # Assert
    assert previous is False


def test_an_offset_past_the_end_is_clamped_to_the_last_page():
    """A bookmarked page of a project that has since shrunk must show that
    project's remainder, not an empty board the reader reads as "empty project"."""
    # Arrange
    rows = _alice_rows(3)
    # Act
    state = _paged_state(rows, {"project": "proj-alpha", "offset": "99999"})
    # Assert
    assert (state.offset, len(state.rows)) == (0, 3)


def test_a_junk_offset_is_treated_as_the_first_page():
    """"?offset=banana" is a hand-edited link: the board is the honest answer."""
    # Arrange
    rows = _alice_rows(2)
    # Act
    state = _paged_state(rows, {"project": "proj-alpha", "offset": "banana"})
    # Assert
    assert state.offset == 0


def test_the_offset_reaches_the_page_start():
    """Paging is a SQL OFFSET, not a slice of a bigger fetch — the rows returned
    for the second page are the ones after the first page's end."""
    # Arrange
    rows = _alice_rows(5)
    # Act
    state = _paged_state(rows, {"project": "proj-alpha", "offset": "2"})
    # Assert
    assert [row["id"] for row in state.rows] == ["alice-0002", "alice-0003", "alice-0004"]


def test_the_page_says_which_cards_it_is_showing():
    """The reader is told the slice, not left to infer it from a bare count."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(3)), total=42, offset=500, page_size=3)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert ('data-stx-shown-from="501"' in body, 'data-stx-shown-to="503"' in body,
            'data-stx-total="42"' in body) == (True, True, True)


def test_the_page_offers_a_next_link_when_there_is_one():
    """The way forward is a link, so it works with the keyboard and without
    JavaScript — the same rule the rest of this page follows."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(2)), total=99, offset=0, page_size=2)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert 'data-stx-page-next' in body


def test_no_pager_when_everything_fits_on_one_page():
    """A pager on a project that fits is a control that cannot do anything."""
    # Arrange
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="p",
                          rows=tuple(_alice_rows(2)), total=2, offset=0, page_size=2)
    # Act
    body = _render(state).content.decode()
    # Assert
    assert 'data-stx-pager' not in body


# --- comments on a card (slice 2e) -------------------------------------------


def test_comment_refuses_a_card_that_is_not_on_this_board():
    """Same boundary as every other card action: the id is a lookup key into the
    rows this page rendered, so another tenant's card is refused before any write."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "bob-b", "text": "hello"}
    # Act
    result = pb.comment_card(_board(), form, comment=recorder)
    # Assert
    assert (result.ok, recorder.calls) == (False, [])


def test_comment_signs_with_the_principal_not_the_forms_author():
    """The store keeps an author on every entry; a page that let the client name it
    would let a comment be signed by someone else."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "alice-a", "text": "hello", "by": "dana", "author": "dana"}
    # Act
    pb.comment_card(_board(), form, comment=recorder)
    # Assert
    assert recorder.calls[0]["by"] == "alice"


def test_comment_names_the_card_it_was_given():
    """task_id reaches the store as the id that was validated."""
    # Arrange
    recorder = _Recorder()
    # Act
    pb.comment_card(_board(), {"card_id": "alice-a", "text": "hello"}, comment=recorder)
    # Assert
    assert recorder.calls[0]["task_id"] == "alice-a"


def test_comment_refuses_an_empty_one():
    """A blank entry in an audit log is noise the next reader has to skip."""
    # Arrange
    form = {"card_id": "alice-a", "text": "   "}
    # Act
    result = pb.comment_card(_board(), form)
    # Assert
    assert result.ok is False


def test_comment_refuses_one_past_the_page_limit():
    """A comment is a note to the next reader, not a document."""
    # Arrange
    form = {"card_id": "alice-a", "text": "x" * (pb.COMMENT_MAX + 1)}
    # Act
    result = pb.comment_card(_board(), form)
    # Assert
    assert result.ok is False


def test_comment_refuses_when_the_board_is_not_open():
    """A denied project must not accept a comment just because a form was posted."""
    # Arrange
    state = pb.BoardState(state=pb.DENIED, principal="alice", is_staff=False)
    # Act
    result = pb.comment_card(state, {"card_id": "alice-a", "text": "hello"})
    # Assert
    assert result.ok is False


def test_the_card_page_shows_the_comments_it_was_given():
    """The detail page is where a card's history lives."""
    # Arrange
    comments = [{"author": "operator", "ts": "2026-09-17T00:00:00Z", "text": "take a look"}]
    # Act
    body = _card_page(state=_board(), comments=comments).content.decode()
    # Assert
    assert ('data-stx-comment-text' in body, "take a look" in body) == (True, True)


def test_the_card_page_says_when_there_are_no_comments():
    """An empty list and a failed read look the same to a reader unless the page
    says which one it is."""
    # Arrange
    state = _board()
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert 'data-stx-comments-empty' in body


def test_the_comment_form_names_the_principal_as_the_author():
    """The author is decided server-side, and the form says so rather than
    pretending to let the reader choose."""
    # Arrange
    state = _board()
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert "Add a comment as alice" in body


def test_no_comment_form_on_a_page_with_no_card_to_comment_on():
    """Controls that cannot work must not be rendered."""
    # Arrange
    state = _state_only(pb.DENIED)
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert 'data-stx-comment-form' not in body


def test_a_refused_comment_answers_400_and_says_why():
    """The reader's input was wrong, and the page says so rather than only the code."""
    # Arrange
    state = _board()
    # Act
    response = _card_page(state=state, comment_error="A comment needs something in it.")
    # Assert
    assert (response.status_code, "A comment needs something in it." in response.content.decode()) == (400, True)
