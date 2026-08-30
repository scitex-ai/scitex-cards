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

NO ``monkeypatch`` ANYWHERE (STX-NM002). The defect under test WAS an
environment disagreement, so a test that patched the environment would be
testing the patch. The :func:`environment` fixture writes real values into the
real ``os.environ`` the resolver reads and restores them on teardown.
"""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

import os
import time
import pytest

from scitex_cards._db import ENV_DB
from scitex_cards._store import resolve_store
from scitex_cards._store_instance import Certainty, IdentityVerdict
from scitex_cards._store_uuid import ENV_EXPECTED_STORE_UUID
from scitex_cards._store_pin import (
    ENV_PINNED_INSTANCE,
    StoreIdentityRefused,
    check_resolution,
    instance_at,
    pinned_instance,
    require_pinned_store,
)

#: THE HARNESS'S STORE, NOT A SECOND NAME FOR ONE. This read
#: ``$SCITEX_CARDS_TEST_PG_DSN`` -- this package's own private marker -- and
#: SKIPPED when it was unset, which is now always: nothing sets that name any
#: more. The five tests below therefore reported green in CI without opening a
#: connection, which is the exact failure
#: ``.github/workflows/postgres-backend-on-ubuntu-latest.yml`` was written to
#: remove ("a Postgres-only test does not FAIL without a server, it SKIPS, and
#: a skipped test is indistinguishable from a passing one").
#:
#: ONE NAME NOW ANSWERS "WHERE IS THE STORE": ``$SCITEX_CARDS_DB``, which
#: ``tests/conftest.py`` pins per test to a throwaway PostgreSQL schema. A
#: second name could disagree with the first, and two names resolving
#: differently is how this repo lost its live board on 2026-07-19.

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
    ``monkeypatch``): the resolver reads the process environment, so the test
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
    """The live PostgreSQL store this test was given. NEVER SKIPS.

    Whatever ``$SCITEX_CARDS_DB`` names is the store, so that is what a test
    about store IDENTITY must interrogate. When no cluster could be opened the
    harness pins a target the doors refuse rather than unsetting the variable,
    so the fixtures below fail naming the unreadable identity instead of
    quietly not running -- which is the contract this file's header describes
    and the old skip quietly broke.
    """
    return os.environ[ENV_DB]


@pytest.fixture
def live_instance_id(pg_dsn):
    """The declared server's REAL system_identifier, read from the server."""
    observed = instance_at(pg_dsn)
    if observed.certainty is not Certainty.KNOWN:
        pytest.fail(
            f"{ENV_DB} names a store whose instance is unreadable: "
            f"{observed.reason}"
        )
    return observed.instance_id


@pytest.fixture
def live_store_uuid(pg_dsn):
    """The declared server's REAL store_uuid, read from the server.

    The SECOND half of the identity. Added 2026-08-19 because a pin is now only
    satisfied when BOTH halves agree: the instance says which SERVER and the
    uuid says which BOARD, and a client that pins one gets ``CANNOT_TELL``
    rather than a half-checked pass.
    """
    from scitex_cards._store_uuid import store_uuid_at

    # SKIPS, rather than FAILS, when the declared server carries no store uuid —
    # and the asymmetry with `live_instance_id` above is the point.
    #
    # `system_identifier` is a property of any live PostgreSQL CLUSTER, so its
    # absence really is a broken declaration and failing is right. `store_uuid`
    # is a `schema_meta` ROW: it exists only once a store has been bootstrapped.
    # A bare `postgres:16` service container — exactly what the postgres-backend
    # CI job spins up — has an instance and NO store, which is a legitimate
    # state. My first version called `pytest.fail` here and turned that
    # legitimate state into a red leg on the first CI run.
    #
    # WHY A SKIP IS ACCEPTABLE HERE, WHICH IT USUALLY IS NOT. A skipped POSITIVE
    # CONTROL is normally indistinguishable from a passing one — that is the
    # defect this very file is about. It is acceptable ONLY because the positive
    # control for the both-halves contract does not live here any more:
    # `test__identity_decision_both_halves.py` proves MATCH is reachable with no
    # database at all, and runs everywhere. What is skipped here is the
    # INTEGRATION check that `resolve_store` wires the pair through — which
    # genuinely cannot be demonstrated against a server with no store on it.
    #
    # To make these run in CI, the job must bootstrap a store at the DSN. That
    # is a change to the workflow's contract, not to this fixture, and it is
    # carded rather than smuggled in here.
    observed = store_uuid_at(pg_dsn)
    if not observed:
        pytest.skip(
            f"{ENV_DB} names a server with no store on it (no "
            "schema_meta.store_uuid), so a BOTH-HALVES pin cannot be satisfied "
            "against it. The contract's positive control lives in "
            "test__identity_decision_both_halves.py and needs no server."
        )
    return observed


#: A target that is not the store. Not a fixture and not created: the point of
#: the two tests that use it is that a target the resolver cannot open reports
#: UNKNOWN rather than inventing an identity, and MAKING one would be the
#: opposite of the case. (`_create_sqlite_store` used to build a real file store
#: here; there is no such thing to build now.)
_NOT_A_STORE = "/nonexistent/scitex-cards/cards.db"


@pytest.fixture
def two_stores(new_store):
    """Two REAL, DIFFERENT stores. No mocks (STX-NM).

    Two throwaway schemas on the cluster the harness opened, each provisioned
    through the package's own ``connect`` + ``init_schema`` -- so they are
    stores the resolver would genuinely accept, and they are genuinely
    DIFFERENT, which is the whole property under test. Two scratch FILES were
    what this built before; a filename names no store, so the fixture could not
    hand out one store, let alone two that differ.
    """
    return [new_store("cards_pin_alpha"), new_store("cards_pin_beta")]


@pytest.fixture
def two_resolutions(environment, two_stores):
    """The same call, twice, under two genuinely different environments."""
    alpha, beta = two_stores
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
def test_an_unpinned_resolution_may_not_proceed(environment, two_stores):
    """No pin is CANNOT_TELL, and CANNOT_TELL is not a pass.

    The defect being closed is precisely that an absent expectation read as
    agreement: ``expected_uuid`` was ``None``, so the comparison it fed had no
    right-hand side and could not fail.
    """
    # Arrange
    alpha, _ = two_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["may_proceed"] is False


def test_an_unpinned_resolution_is_named_cannot_tell(environment, two_stores):
    """The verdict is NAMED, so a caller must handle it rather than inherit it."""
    # Arrange
    alpha, _ = two_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["identity_verdict"] == IdentityVerdict.CANNOT_TELL.value


def test_an_unpinned_resolution_says_why(environment, two_stores):
    """A refusal a caller cannot print is a refusal nobody can act on."""
    # Arrange
    alpha, _ = two_stores
    environment(**{ENV_DB: alpha, ENV_PINNED_INSTANCE: None})
    # Act
    report = resolve_store()
    # Assert
    assert report["identity_reason"]


def test_resolve_store_reports_the_pin_it_was_given(environment, two_stores):
    """``expected_instance`` echoes the pin, so a mismatch shows both sides."""
    # Arrange
    alpha, _ = two_stores
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


def test_a_target_that_is_not_the_store_cannot_report_an_instance():
    """No cluster to ask, so there is no instance identity to be had.

    THE SUBJECT SURVIVED THE ENGINE, and only the example changed. It was "a
    byte copy of a FILE is indistinguishable from the original inside it" --
    file identity, which is why the fixture built one. A file is now simply a
    target the resolver refuses, and the property that matters is the same one:
    a target that cannot be opened must answer UNKNOWN rather than invent a
    stable-looking value. Passing a path that does not exist makes that
    unambiguous; passing a REAL store would test the opposite branch.
    """
    # Arrange
    target = _NOT_A_STORE
    # Act
    observed = instance_at(target)
    # Assert
    assert observed.certainty is Certainty.UNKNOWN


def test_a_target_that_is_not_the_store_says_why_it_cannot_answer():
    """The honest answer, with its reason — never an invented stable value.

    Asserted against the reason's own words rather than an engine name. It used
    to look for "the retired engine" in the string; the reason names no engine now, because
    there is only one and the answer is about this target not being it.
    """
    # Arrange
    target = _NOT_A_STORE
    # Act
    observed = instance_at(target)
    # Assert
    assert "does not name the store" in observed.reason


# ---------------------------------------------------------------------------
# Against a real server
# ---------------------------------------------------------------------------
def test_a_correct_pin_matches_a_live_server(
    environment, pg_dsn, live_instance_id, live_store_uuid
):
    """The happy path is reachable — a pinned client can still work.

    PINS BOTH HALVES, a DELIBERATE update made 2026-08-19 rather than a repair.
    Until then this pinned the instance alone and expected ``MATCHES``; an
    instance-only pin is now ``CANNOT_TELL``, because the instance identifies
    the SERVER and a database restored onto that same server keeps it while
    getting a new uuid. This test's PURPOSE is unchanged, which is why it was
    updated rather than deleted: it is the case asserting a PASS, and without
    it a guard that refuses everything would look correct.
    """
    # Arrange
    environment(
        **{
            ENV_DB: pg_dsn,
            ENV_PINNED_INSTANCE: live_instance_id,
            ENV_EXPECTED_STORE_UUID: live_store_uuid,
        }
    )
    # Act
    check = check_resolution()
    # Assert
    assert check.verdict is IdentityVerdict.MATCHES


def test_a_correct_pin_permits_the_resolution(
    environment, pg_dsn, live_instance_id, live_store_uuid
):
    """A guard that only ever refuses is not a guard, it is an outage.

    Both halves pinned, for the reason on
    ``test_a_correct_pin_matches_a_live_server``.
    """
    # Arrange
    environment(
        **{
            ENV_DB: pg_dsn,
            ENV_PINNED_INSTANCE: live_instance_id,
            ENV_EXPECTED_STORE_UUID: live_store_uuid,
        }
    )
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
    environment, pg_dsn, live_instance_id, live_store_uuid
):
    """The permitted path returns the target every write should then use.

    Both halves pinned, for the reason on
    ``test_a_correct_pin_matches_a_live_server``. This is the REFUSING door's
    positive control, so it is the one that proves the door can still open.
    """
    # Arrange
    environment(
        **{
            ENV_DB: pg_dsn,
            ENV_PINNED_INSTANCE: live_instance_id,
            ENV_EXPECTED_STORE_UUID: live_store_uuid,
        }
    )
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
    environment, pg_dsn, live_instance_id, live_store_uuid
):
    """The AGREE branch of the property, against a real server.

    The disjunction is only meaningful if both branches occur: this pins that a
    correctly-configured environment is permitted, so the headline test cannot
    be satisfied by a guard that simply refuses everything.

    "Correctly configured" now means BOTH halves pinned (2026-08-19). A
    deliberate contract change, not a repair — see
    ``test_a_correct_pin_matches_a_live_server``.
    """
    # Arrange
    environment(
        **{
            ENV_DB: pg_dsn,
            ENV_PINNED_INSTANCE: live_instance_id,
            ENV_EXPECTED_STORE_UUID: live_store_uuid,
        }
    )
    # Act
    report = resolve_store()
    # Assert
    assert report["may_proceed"] is True


# EOF
