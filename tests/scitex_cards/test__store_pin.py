#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolving twice under two environments must AGREE or REFUSE — never differ quietly.

THE TEST THIS FILE EXISTS FOR is
``test_resolving_under_two_environments_never_silently_yields_two_databases``.
Everything else here supports it.

WHAT IT WOULD HAVE CAUGHT, measured 2026-08-12. One agent's MCP server and its
own container shell disagreed about ``$SCITEX_CARDS_DB`` — ``:5442`` versus
``:55432`` — because the value was baked into a materialised ``.mcp.json`` from
whichever shell ``sac agents start`` was typed in. Two resolutions, two
different PostgreSQL clusters, no error on either side. The agent wrote cards to
one database and read them back from another, and ``comment_task`` on a card it
had created forty minutes earlier reported the id "not found".

Nothing detected it because every identity field a client could read was
byte-identical. THREE live databases answered ``store_uuid =
1d55dd6e-3d2a-4c24-a429-a78835ab988f`` while holding 3843, 3743 and 3422 cards
— ``store_uuid`` is a ``schema_meta`` ROW, and a dump/restore carries rows.

So this file does NOT assert "the two resolutions are equal". Two stores is a
legitimate state (a laptop and a server, a test store and a real one); the
defect is that the difference was SILENT. The property asserted is the weaker
and truer one, and it is the one the deployment actually violated:

    for any two environments, either the resolutions agree, or at least one of
    them refuses to proceed.

A pass therefore means "you cannot end up on the wrong store without being
told", not "there is only one store".

NO ``env`` ANYWHERE (STX-NM002). The defect under test WAS an
environment disagreement, so a test that patched the environment would be
testing the patch. The :func:`environment` fixture writes real values into the
real ``os.environ`` the resolver reads and restores them on teardown.
"""

from __future__ import annotations

import os
import time
from contextlib import closing
from pathlib import Path

import pytest

from scitex_cards._db import ENV_DB, connect
from scitex_cards._store import resolve_store
from scitex_cards._store_instance import Certainty, IdentityVerdict
from scitex_cards._store_pin import (
    ENV_PINNED_INSTANCE,
    StoreIdentityRefused,
    check_resolution,
    instance_at,
    pinned_instance,
    require_pinned_store,
)

#: Same contract as ``test__store_instance`` and ``test__sql_null_safe``:
#: UNDECLARED skips, DECLARED-but-broken fails. A Postgres-only test that skips
#: is indistinguishable from a passing one in a green summary.
_ENV_PG_DSN = "SCITEX_CARDS_TEST_PG_DSN"

#: A DSN pointing at a port nothing serves. Pins the "unreachable target answers
#: UNKNOWN rather than raising or hanging" contract. Port 1 is privileged and
#: unbound on every machine this suite runs on.
_DEAD_DSN = "postgresql://scitex_cards@127.0.0.1:1/scitex_cards"

#: A well-formed ``system_identifier`` that is not any real server's. Used to
#: pin the DIFFERS branch: a live connection to a live server, judged against an
#: expectation it genuinely does not meet. Not a mock — the server is real and
#: the answer is real; only the expectation is deliberately wrong.
#:
#: DO NOT paste a real cluster's identifier here. The first draft used
#: ``7672112238472680366``, copied from the compute-04 store this was developed
#: against, and the DIFFERS test failed with MATCHES — because that WAS the
#: server it connected to. A "wrong" value taken from the environment is only
#: wrong until someone runs the suite on the machine it came from.
_FOREIGN_INSTANCE = "7600000000000000001"


@pytest.fixture
def environment():
    """Set REAL process env vars; restore the prior values on teardown.

    Yield-based and ``os.environ``-backed on purpose (STX-NM002 forbids
    ``env``): the resolver reads the process environment, so the test
    must write the process environment or it is not exercising the precedence
    chain that broke.
    """
    saved: dict[str, str | None] = {}

    def apply(**pairs: str | None) -> None:
        for key, value in pairs.items():
            if key not in saved:
                saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    yield apply
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def pg_dsn():
    """A live Postgres DSN, or a skip when none is declared."""
    dsn = os.environ.get(_ENV_PG_DSN)
    if not dsn:
        pytest.skip(f"{_ENV_PG_DSN} is not set — no Postgres declared")
    return dsn


@pytest.fixture
def live_instance_id(pg_dsn):
    """The declared server's REAL system_identifier, read from the server."""
    observed = instance_at(pg_dsn)
    if observed.certainty is not Certainty.KNOWN:
        pytest.fail(
            f"{_ENV_PG_DSN} is declared but its instance is unreadable: "
            f"{observed.reason}"
        )
    return observed.instance_id


def _create_sqlite_store(path: Path) -> str:
    """Create ONE real store at ``path``; return the path, never the handle.

    The connection lives and dies inside this function, inside ``closing`` —
    so it is released even if ``connect`` hands back a store that raises on
    use. This is deliberately NOT a fixture: a fixture that acquires a
    resource has to give it to the test via ``yield`` so pytest can tear it
    down (STX-TQ005), and the only way to owe no teardown is to own the
    whole lifetime here. What escapes is a ``str`` path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(str(path))):
        pass
    return str(path)


@pytest.fixture
def two_sqlite_stores(tmp_path):
    """Two REAL, DIFFERENT SQLite stores on disk. No mocks (STX-NM).

    Created through the package's own ``connect`` so they are stores the
    resolver would genuinely accept, not empty files with the right names.
    """
    return [
        _create_sqlite_store(tmp_path / name / "cards.db")
        for name in ("alpha", "beta")
    ]


@pytest.fixture
def two_resolutions(environment, two_sqlite_stores):
    """The same call, twice, under two genuinely different environments."""
    alpha, beta = two_sqlite_stores
    environment(**{ENV_DB: alpha})
    first = resolve_store()
    environment(**{ENV_DB: beta})
    second = resolve_store()
    return first, second


# ---------------------------------------------------------------------------
# THE PROPERTY
# ---------------------------------------------------------------------------
def test_resolving_under_two_environments_never_silently_yields_two_databases(
    two_resolutions,
):
    """Two environments, two stores — and the difference must not be silent.

    The regression test for the 2026-08-12 three-database split. It asserts the
    DISJUNCTION rather than equality, because two stores is a legal state and an
    unannounced second store is not.
    """
    # Arrange
    first, second = two_resolutions
    # Act
    agreed = first["resolved"] == second["resolved"]
    refused = not (first["may_proceed"] and second["may_proceed"])
    # Assert
    assert agreed or refused, (
        "two environments resolved to two different databases and BOTH "
        f"reported may_proceed=True: {first['resolved']} vs "
        f"{second['resolved']}. That is the silent split this test exists to "
        "prevent — a client that cannot tell it moved will write to one store "
        "and read from the other."
    )


def test_the_two_environments_really_did_reach_different_stores(two_resolutions):
    """Guard the guard: the property must not pass by resolving equal.

    Without this, a resolver that ignored ``$SCITEX_CARDS_DB`` entirely would
    satisfy the disjunction through its ``agreed`` branch, and the suite would
    go green on a store nobody chose.
    """
    # Arrange
    first, second = two_resolutions
    # Act
    resolved_pair = (first["resolved"], second["resolved"])
    # Assert
    assert resolved_pair[0] != resolved_pair[1]


# ---------------------------------------------------------------------------
# An absent expectation is not agreement
# ---------------------------------------------------------------------------
def test_an_unpinned_resolution_may_not_proceed(environment, two_sqlite_stores):
    """No pin is CANNOT_TELL, and CANNOT_TELL is not a pass.

    The defect being closed is precisely that an absent expectation read as
    agreement: ``expected_uuid`` was ``None``, so the comparison it fed had no
    right-hand side and could not fail.
    """
    # Arrange
    alpha, _ = two_sqlite_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["may_proceed"] is False


def test_an_unpinned_resolution_is_named_cannot_tell(environment, two_sqlite_stores):
    """The verdict is NAMED, so a caller must handle it rather than inherit it."""
    # Arrange
    alpha, _ = two_sqlite_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["identity_verdict"] == IdentityVerdict.CANNOT_TELL.value


def test_an_unpinned_resolution_says_why(environment, two_sqlite_stores):
    """A refusal a caller cannot print is a refusal nobody can act on."""
    # Arrange
    alpha, _ = two_sqlite_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["identity_reason"]


def test_resolve_store_reports_the_pin_it_was_given(environment, two_sqlite_stores):
    """``expected_instance`` echoes the pin, so a mismatch shows both sides."""
    # Arrange
    alpha, _ = two_sqlite_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: _FOREIGN_INSTANCE})
    # Act
    report = resolve_store()
    # Assert
    assert report["expected_instance"] == _FOREIGN_INSTANCE


# ---------------------------------------------------------------------------
# The pin
# ---------------------------------------------------------------------------
def test_a_blank_pin_is_no_pin_rather_than_the_empty_identity(environment):
    """A variable that exists but was never filled in must not refuse everything."""
    # Arrange
    environment(**{ENV_PINNED_INSTANCE: "   "})
    # Act
    got = pinned_instance()
    # Assert
    assert got is None


def test_a_pin_is_returned_verbatim_and_never_normalised(environment):
    """Normalising the expectation is normalising the comparison."""
    # Arrange
    environment(**{ENV_PINNED_INSTANCE: " 7671108644284358700 "})
    # Act
    got = pinned_instance()
    # Assert
    assert got == " 7671108644284358700 "


def test_an_explicit_pin_outranks_the_environment(environment):
    """Explicit argument, then env, then absent — the family's shared order."""
    # Arrange
    environment(**{ENV_PINNED_INSTANCE: "from-env"})
    # Act
    got = pinned_instance("from-argument")
    # Assert
    assert got == "from-argument"


# ---------------------------------------------------------------------------
# The probe — reporting contract
# ---------------------------------------------------------------------------
def test_an_unreachable_server_answers_unknown_instead_of_raising():
    """A diagnostic that throws on the broken case is worse than none."""
    # Arrange
    target = _DEAD_DSN
    # Act
    observed = instance_at(target)
    # Assert
    assert observed.certainty is Certainty.UNKNOWN


def test_an_unreachable_server_carries_no_invented_identity():
    """UNKNOWN must be None, so two unknowns can never compare equal."""
    # Arrange
    target = _DEAD_DSN
    # Act
    observed = instance_at(target)
    # Assert
    assert observed.instance_id is None


def test_an_unreachable_server_states_its_cause():
    """The caller must be able to tell a dead port from a missing capability."""
    # Arrange
    target = _DEAD_DSN
    # Act
    observed = instance_at(target)
    # Assert
    assert observed.reason


def test_an_unreachable_server_answers_promptly():
    """The bound is contract, not tuning: libpq applies no connect timeout.

    Measured >40s against a dead port before ``store_uuid_at`` grew the same
    bound. ``resolve-store`` is what someone runs WHEN THINGS ARE ALREADY
    BROKEN, so hanging is not a lesser failure than answering wrongly.
    """
    # Arrange
    started = time.monotonic()
    # Act
    instance_at(_DEAD_DSN)
    # Assert
    assert time.monotonic() - started < 30


def test_a_sqlite_target_cannot_report_an_instance(two_sqlite_stores):
    """A byte copy of a file is indistinguishable from the original inside it."""
    # Arrange
    alpha, _ = two_sqlite_stores
    # Act
    observed = instance_at(alpha)
    # Assert
    assert observed.certainty is Certainty.UNKNOWN


def test_a_sqlite_target_says_why_it_cannot_answer(two_sqlite_stores):
    """The honest answer, with its reason — never an invented stable value."""
    # Arrange
    alpha, _ = two_sqlite_stores
    # Act
    observed = instance_at(alpha)
    # Assert
    assert "sqlite" in observed.reason


# ---------------------------------------------------------------------------
# Against a real server
# ---------------------------------------------------------------------------
def test_a_correct_pin_matches_a_live_server(environment, pg_dsn, live_instance_id):
    """The happy path is reachable — a pinned client can still work."""
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: live_instance_id})
    # Act
    check = check_resolution()
    # Assert
    assert check.verdict is IdentityVerdict.MATCHES


def test_a_correct_pin_permits_the_resolution(environment, pg_dsn, live_instance_id):
    """A guard that only ever refuses is not a guard, it is an outage."""
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: live_instance_id})
    # Act
    check = check_resolution()
    # Assert
    assert check.may_proceed is True


def test_a_wrong_pin_differs_from_a_live_server(environment, pg_dsn):
    """The refusal the 2026-08-12 split needed and did not have.

    A live connection to a live server, judged against an expectation it
    genuinely does not meet — the shape of "you were pinned to store A and
    reached store B".
    """
    # Arrange
    environment(**{ENV_DB: pg_dsn})
    # Act
    check = check_resolution(expected=_FOREIGN_INSTANCE)
    # Assert
    assert check.verdict is IdentityVerdict.DIFFERS


def test_a_wrong_pin_forbids_the_resolution(environment, pg_dsn):
    """DIFFERS refuses — the verdict is not merely informational."""
    # Arrange
    environment(**{ENV_DB: pg_dsn})
    # Act
    check = check_resolution(expected=_FOREIGN_INSTANCE)
    # Assert
    assert check.may_proceed is False


def test_a_wrong_pin_names_both_sides(environment, pg_dsn):
    """Print both values rather than assert a mismatch the reader cannot check."""
    # Arrange
    environment(**{ENV_DB: pg_dsn})
    # Act
    check = check_resolution(expected=_FOREIGN_INSTANCE)
    # Assert
    assert _FOREIGN_INSTANCE in check.reason


def test_require_pinned_store_raises_on_a_wrong_pin(environment, pg_dsn):
    """The refusal DOOR actually refuses — the report alone changes nothing."""
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: _FOREIGN_INSTANCE})
    # Act
    act = require_pinned_store
    # Assert
    with pytest.raises(StoreIdentityRefused):
        act()


def test_require_pinned_store_raises_when_nothing_is_pinned(environment, pg_dsn):
    """CANNOT_TELL refuses alongside DIFFERS, via ``may_proceed``.

    A call site written as ``verdict is not DIFFERS`` would let this through,
    which is the collapse that kept three databases indistinguishable.
    """
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: None})
    # Act
    act = require_pinned_store
    # Assert
    with pytest.raises(StoreIdentityRefused):
        act()


def test_require_pinned_store_returns_the_target_when_the_pin_holds(
    environment, pg_dsn, live_instance_id
):
    """The permitted path returns the target every write should then use."""
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: live_instance_id})
    # Act
    got = require_pinned_store()
    # Assert
    assert got == pg_dsn


def test_resolve_store_reports_a_live_servers_instance(
    environment, pg_dsn, live_instance_id
):
    """``instance_id`` is the value ``store_uuid`` could not provide.

    The field an operator copies into a registry so the next client can pin it —
    the machine-readable half that ends the archaeology.
    """
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: live_instance_id})
    # Act
    report = resolve_store()
    # Assert
    assert report["instance_id"] == live_instance_id


def test_a_correctly_pinned_resolution_may_proceed(
    environment, pg_dsn, live_instance_id
):
    """The AGREE branch of the property, against a real server.

    The disjunction is only meaningful if both branches occur: this pins that a
    correctly-configured environment is permitted, so the headline test cannot
    be satisfied by a guard that simply refuses everything.
    """
    # Arrange
    environment(**{ENV_DB: pg_dsn, ENV_PINNED_INSTANCE: live_instance_id})
    # Act
    report = resolve_store()
    # Assert
    assert report["may_proceed"] is True


# EOF
