"""Identical real-PG red/green controls for canonical Cards row hazards.

The baseline uses only APIs that already exist. Red means the actual canonical
parent/children/hash state violates the contract, never a missing new API.
"""

import json
from copy import deepcopy

import pytest
from _card_row_pg_support import (
    card,
    connection,
    enroll,
    enrollment_while_paused,
    paused_client,
    seed,
    snapshot,
    winner,
)

from scitex_cards._db_bootstrap import _insert_tasks
from scitex_cards._mirror_rows import _delete_card, _write_card
from scitex_cards._store_single_card import _read_card_with_connection


@pytest.fixture
def row_store(new_store):
    dsn = new_store("cards_row_cas")
    seed(dsn)
    return dsn


def test_late_revision_loser_preserves_winner_derived_rows(row_store, record_property):
    # Arrange
    revision = snapshot(row_store)["parent"]["revision"]
    with paused_client(
        row_store,
        lambda conn: _write_card(conn, card("loser"), expected_revision=revision),
        record_property,
    ) as client:
        winner(row_store, revision)
        expected = snapshot(row_store)
        # Act: the mirror caller deliberately commits a returned skip outcome.
        client.finish()
    # Assert
    assert snapshot(row_store) == expected


def test_revision_update_cannot_resurrect_a_deleted_parent(row_store, record_property):
    # Arrange
    revision = snapshot(row_store)["parent"]["revision"]
    with paused_client(
        row_store,
        lambda conn: _insert_tasks(
            conn, [card("resurrected")], expected_revision=revision
        ),
        record_property,
        boundary="parent-mutation",
    ) as client:
        with connection(row_store) as other:
            record_property(
                "contender_backend_pid",
                other.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"],
            )
            other.execute("DELETE FROM tasks WHERE id = ?", ("alpha",))
            other.commit()
        # Act
        client.finish()
    # Assert
    assert snapshot(row_store)["parent"] is None


def test_none_existing_write_cannot_clobber_concurrent_enrollment(
    row_store, record_property
):
    # Arrange
    with paused_client(
        row_store,
        lambda conn: _write_card(conn, card("legacy-caller")),
        record_property,
    ) as client:
        # Act: the contender commits before the baseline write, or is proven
        # blocked by the candidate's real parent lock and commits afterwards.
        enrollment_while_paused(row_store, client, record_property)
    payload = json.loads(snapshot(row_store)["parent"]["card_json"])
    # Assert
    assert payload.get("_log_meta") == {
        "delivery": {"fixture_generation": "owned-enrollment"}
    }


def test_none_absent_write_cannot_overwrite_concurrent_enrollment(
    new_store, record_property
):
    # Arrange
    dsn = new_store("cards_absent_enrollment")
    seed(dsn, present=False)
    with paused_client(
        dsn,
        lambda conn: _write_card(conn, card("absent-legacy-caller")),
        record_property,
        allowed_errors=("CardAlreadyExistsError",),
    ) as client:
        with connection(dsn) as other:
            record_property(
                "contender_backend_pid",
                other.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"],
            )
            enroll(other, absent=True)
        expected = snapshot(dsn)
        # Act
        client.finish()
    # Assert
    assert snapshot(dsn) == expected


def test_generic_remove_cannot_delete_concurrent_enrollment(row_store, record_property):
    # Arrange
    with paused_client(
        row_store,
        lambda conn: _delete_card(conn, "alpha"),
        record_property,
        allowed_errors=("RevisionConflictError",),
    ) as client:
        with connection(row_store) as other:
            record_property(
                "contender_backend_pid",
                other.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"],
            )
            enroll(other)
        expected = snapshot(row_store)
        # Act
        client.finish()
    # Assert
    assert snapshot(row_store) == expected


@pytest.mark.parametrize(
    "payload",
    ["null", "42", '"synthetic-private-marker"', "[]", "{broken"],
    ids=["json-null", "number", "string", "array", "malformed"],
)
def test_existing_nonobject_payload_is_a_typed_refusal(row_store, payload):
    # Arrange
    with connection(row_store) as conn:
        conn.execute("UPDATE tasks SET card_json = ? WHERE id = ?", (payload, "alpha"))
        conn.commit()
    # Act
    observed = None
    with connection(row_store) as conn:
        try:
            _read_card_with_connection(conn, "alpha")
        except Exception as error:
            observed = type(error).__name__
    # Assert
    assert observed == "CardPayloadShapeError"


def test_none_still_writes_a_genuinely_legacy_existing_row(row_store):
    # Arrange
    with connection(row_store) as conn:
        stale = deepcopy(card("intentional-lww"))
        # Act
        _write_card(conn, stale, expected_revision=None)
        conn.commit()
    # Assert
    assert (
        json.loads(snapshot(row_store)["parent"]["card_json"])["title"]
        == "intentional-lww"
    )
