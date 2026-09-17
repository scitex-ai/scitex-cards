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


def _state(viewer, rows, params=None, *, is_staff=False, provider=None, session=None):
    request = _Request(_User(viewer, is_staff=is_staff), params, session)
    return pb.board_state(request, loader=_loader(rows), provider=provider)


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
    state = pb.board_state(_Request(_User("alice")), loader=_boom)
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


def _render(state, *, path="/projects", user="alice", host_picker_available=None):
    """Render a state through the REAL page view, no patching.

    ``render_project_board`` is the production function the view delegates to;
    calling it with a constructed state is how a test asks "what does the page
    answer in THIS state" without a store, a request cycle or a mock.
    """
    from django.test import RequestFactory

    request = RequestFactory().get(path, HTTP_HOST="127.0.0.1")
    request.user = _User(user) if user else None
    return pb.render_project_board(request, state, host_picker_available=host_picker_available)


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
