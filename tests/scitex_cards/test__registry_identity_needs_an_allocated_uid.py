#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The registry cannot be made fleet-global until an ALLOCATED agent uid exists.

THIS FILE IS A BLOCK THAT EXPIRES VISIBLY. It is red on purpose, and it turns
"I am blocked on another package" — a verdict only a human re-reading a card
can retire — into a condition the suite re-evaluates on every run. When sac
allocates `agent_uid` in the spec, these go green with no edit here, and the
retrofit they gate becomes runnable. Until then they name exactly what is
missing and why the obvious substitutes are wrong.

■ WHY THE REGISTRY IS STILL PER-HOST AFTER MOVING IT TO THE DATABASE

`users` carries none of the sync columns and is not in `SYNCED_TABLES`, so PR
#897 made the registry DATABASE-BACKED without making it FLEET-GLOBAL. Adding
the five columns is trivial. Choosing what makes a user row unique ACROSS
hosts is not, and getting it wrong is unrecoverable: two ids for one agent
cannot be reconciled afterwards, because nothing records which was real.

■ THE RULE (scitex-dev's store contract, PR scitex-dev#682 §3)

    A cross-host key must be STABLE UNDER RELOCATION and UNRECOMPUTABLE AT
    THE POINT OF USE. Derive it from an intrinsic attribute where one exists.
    Where none exists, ALLOCATE it once, at the authority that creates the
    entity, and have the entity CARRY it thereafter.

The distinction that matters, and the one this file exists to protect:

    minting as LOCAL INFERENCE   each host independently computes an id
                                 -> diverges under partition. THE DEFECT.
    allocation by an AUTHORITY   one issuer allocates once; the entity
                                 carries it -> nobody recomputes it, so
                                 there is nothing to diverge.

A passport number is not derived from the person and is still sound identity.
What makes an id unsafe is not that it was assigned — it is that it is
re-derived or re-assigned at each point of use.

■ WHY EVERY AVAILABLE KEY FAILS TODAY — measured 2026-08-17, not assumed

    agent NAME          NOT fleet-unique. sac (their measurement): the registry
                        is per-host SQLite, so cross-host uniqueness is
                        unenforceable AT REGISTRATION by construction;
                        `instances.name` has no UNIQUE; and `canary-resume-test`
                        ran on TWO HOSTS FOR ~8.5 HOURS, ended by a HAND-TYPED
                        exit_reason. A human was the constraint.
                        (Four duplicate names in ONE host's DB — a LOWER BOUND.)

    {host}/{name}       WHERE-dependent: the key changes when an agent relocates.

    instances.id (uuid7) WHEN-dependent, and worse, the WRONG NOUN — it identifies
                        an INSTANCE. Relocation writes a new instances row with a
                        new uuid7, so an agent's identity would change when it
                        moves. Same defect as {host}/{name}, better disguised.

    identity from       `instances` is an OBSERVATION table. A per-host
    STATE generally     observation table structurally cannot answer a
                        fleet-global question. This is the root cause: identity
                        is being read out of state. ALLOCATION is design and
                        belongs in the spec (git); OBSERVATION is state and
                        belongs in the database.

■ AN ALLOCATED UID FIXES ONE OF THREE PROBLEMS — DO NOT READ IT AS THE ANSWER

These three look identical from the symptom "two rows, one id", and running
them together is why this was hard to hold:

    1 identity instability   relocation CHANGES the key. uuid7's defect.
                             FIXED by allocation-in-spec. <- what this file gates
    2 name collision         two live agents share one a2a ADDRESS. The 8.5-hour
                             overlap. An ADDRESSING bug. Unaffected by a uid.
    3 cardinality unenforced two live PROCESSES claim one IDENTITY. The
                             2026-08-07 card-store split-brain — two instances,
                             one id, two postgres stores, neither seeing the
                             other's writes. NOT fixed by a uid.

(3) is the one to keep in view here, because a uid makes it WORSE-LOOKING and
no better: sac's own source says a spec `copied to another machine` produces
two agents under one identity with no error anywhere — and a copied spec
carries a GENUINE uid, so both holders are legitimately identical. What
prevents (3) is a LEASE: the right to RUN under an identity, held by one
process at a time, acquired at start and refused to a second claimant. Same
split again — allocation is design (spec, git); the right to run as it is
state (database, leased).

So these tests going green means the registry can be keyed safely. It does not
mean two processes cannot claim the same key.

■ A TRAP THIS FILE ALSO GUARDS

`comms_nodes.name` IS `TEXT PRIMARY KEY` and raises
`CommsNodeConflictError: "ADR-0014: names are globally unique."` Anyone who
greps that docstring concludes names ARE unique. They are not: the cross-host
sync path uses `INSERT OR IGNORE`, so a conflicting row is silently dropped,
first writer wins, no error. It is a COLLISION DETECTOR, not a constraint —
right about intent, wrong about enforcement. Do not let that sentence retire
these tests.
"""

from __future__ import annotations

import pytest


def _sync_columns_and_tables():
    from scitex_cards._db_sync_columns import SYNC_COLUMNS, SYNCED_TABLES

    return {name for name, _ in SYNC_COLUMNS}, set(SYNCED_TABLES)


def _allocated_agent_uid_exists() -> bool:
    """Whether an agent carries an ALLOCATED uid we could key on.

    The unblock condition, in one function. It is deliberately a capability
    probe rather than a version check: it goes true when the thing exists,
    from whatever direction it arrives, and cannot be satisfied by a version
    bump that ships the name without the behaviour.
    """
    try:
        from scitex_cards._users._store_write import allocated_agent_uid
    except ImportError:
        return False
    return callable(allocated_agent_uid)


pytestmark = pytest.mark.xfail(
    not _allocated_agent_uid_exists(),
    reason=(
        "BLOCKED, and this is the unblock condition: no ALLOCATED agent uid "
        "exists yet. Every available key fails the store contract — the agent "
        "NAME is not fleet-unique (measured: two hosts, one name, 8.5 hours), "
        "{host}/{name} is WHERE-dependent, and instances.id (uuid7) is "
        "WHEN-dependent AND identifies an instance rather than an agent, so it "
        "changes on relocation. The fix is sac allocating `agent_uid` once in "
        "the agent SPEC (design, git) rather than deriving identity from the "
        "`instances` observation table (state). When that lands, these pass "
        "with no edit here and the users sync retrofit becomes runnable."
    ),
    strict=False,
)


def test_users_is_a_synced_table() -> None:
    """The registry must participate in cross-host sync to be fleet-global."""
    # Arrange
    _, synced = _sync_columns_and_tables()
    # Act
    present = "users" in synced
    # Assert
    assert present


def test_users_carries_the_sync_columns() -> None:
    """All five, from creation — retrofitting them is the defect this names."""
    # Arrange
    from scitex_cards._db_schema_sql import SCHEMA_SQL

    required, _ = _sync_columns_and_tables()
    users_ddl = SCHEMA_SQL[SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS users") :]
    users_ddl = users_ddl[: users_ddl.index(");")]
    # Act
    missing = {c for c in required if c not in users_ddl}
    # Assert
    assert missing == set()


def test_an_allocated_uid_is_available_to_key_on() -> None:
    """The condition the two tests above are actually waiting for.

    Kept separate so the failure names the CAUSE rather than a symptom. The
    two above describe what the schema should look like; this one describes
    why it cannot be built yet.
    """
    # Arrange
    expected = True
    # Act
    available = _allocated_agent_uid_exists()
    # Assert
    assert available is expected


# EOF
