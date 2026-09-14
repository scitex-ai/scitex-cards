#!/usr/bin/env python3
"""`poll_notifications.outstanding` — the seen-but-unconfirmed set, first-class.

TWO LAYERS, because this node has no writable PostgreSQL:

1. HERMETIC — the pure helper ``outstanding_records_off_page`` (the load-bearing
   diff logic), tested on row dicts with no store, no cursor, no DSN. These run
   anywhere, including a read-only node, and are the verified local evidence.

2. INTEGRATION — the full ``poll_notifications`` payload, gated on
   ``SCITEX_CARDS_TEST_DSN`` (the house precedent for store-touching inbox
   tests: the sibling ``test__inbox_receipt_postgres.py`` uses the same env).
   They run in CI, where a writable throwaway cluster is provided, and are
   skipped (not failed) on a node with none. The backend is the shared
   PostgreSQL inbox (the file rail was retired 2026-08-23), so there is no
   yaml variant.

THE DEFECT THIS SURFACES: the channel drain marks a record ``seen`` the moment
it PUSHES it (``record_push(advance_cursor=True)`` sets ``seen = 1`` in the same
statement that writes ``pushed_at``). So a record handed to a session that then
died is seen, unconfirmed, and ABSENT from the default ``unseen_only=True``
page — yet still named by ``unconfirmed``. ``outstanding`` returns the full
records for those ids, built from the SAME two sources ``unconfirmed`` uses
(``unconfirmed_ids`` + ``receipts``) so the two cannot disagree. Pure read:
advances no cursor, confirms nothing.

NOT #973's half: the ``delivered_at``/``confirmed_at`` cursor split and the
``_inbox_present.pending`` recovery rail are #973's; this change does not touch
either, and is safe to land alongside it.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._inbox_receipt import outstanding_records_off_page

# ===========================================================================
# LAYER 1 — HERMETIC: the pure helper
# ===========================================================================


def _receipt(nid, *, seen=True, pushed_at="t0", confirmed_at=None):
    return {
        "id": nid,
        "event_type": "dm",
        "card_id": "c1",
        "body": "b",
        "actor": "peer",
        "ts": "t0",
        "seen": seen,
        "pushed_at": pushed_at,
        "confirmed_at": confirmed_at,
    }


def test_returns_off_page_outstanding_receipts():
    # Arrange: two outstanding receipts, none on the page.
    recs = {"n1": _receipt("n1"), "n2": _receipt("n2")}
    # Act: diff with an empty page.
    out = outstanding_records_off_page([], ["n1", "n2"], recs, rotate=lambda r: r)
    # Assert: both survive, in the order given.
    assert [r["id"] for r in out] == ["n1", "n2"]


def test_excludes_a_record_already_on_the_page():
    # Arrange: n1 is on the page, n2 is not.
    recs = {"n1": _receipt("n1"), "n2": _receipt("n2")}
    # Act: diff with n1 present.
    out = outstanding_records_off_page([{"id": "n1"}], ["n1", "n2"], recs, rotate=lambda r: r)
    # Assert: only n2 is returned.
    assert [r["id"] for r in out] == ["n2"]


def test_skips_an_outstanding_id_with_no_receipt():
    # Arrange: n9 is outstanding but has no receipt to lift.
    recs = {"n1": _receipt("n1")}
    # Act: diff.
    out = outstanding_records_off_page([], ["n1", "n9"], recs, rotate=lambda r: r)
    # Assert: only the one with a receipt survives.
    assert [r["id"] for r in out] == ["n1"]


def test_applies_the_rotate_callback_to_each_survivor():
    # Arrange: one survivor, a rotate that tags the row.
    recs = {"n1": _receipt("n1")}
    # Act: rotate applied.
    out = outstanding_records_off_page([], ["n1"], recs, rotate=lambda r: dict(r, rotated=True))
    # Assert: the callback ran.
    assert out[0].get("rotated") is True


def test_returns_full_receipt_records_not_bare_ids():
    # Arrange: one receipt carrying the push/confirm fields.
    recs = {"n1": _receipt("n1", seen=True, pushed_at="t0", confirmed_at=None)}
    # Act: diff.
    out = outstanding_records_off_page([], ["n1"], recs, rotate=lambda r: r)
    # Assert: the row is the full record (the point vs `unconfirmed` ids).
    assert out[0]["pushed_at"] == "t0" and out[0]["confirmed_at"] is None


# ===========================================================================
# LAYER 2 — INTEGRATION: the full poll payload (CI: writable test DSN)
# ===========================================================================

TEST_DSN_ENV = "SCITEX_CARDS_TEST_DSN"
_DSN = os.environ.get(TEST_DSN_ENV)

pytest.importorskip("psycopg")

from scitex_cards import _inbox  # noqa: E402
from scitex_cards._inbox_confirm import confirm_notifications  # noqa: E402
from scitex_cards._inbox_receipt import record_push  # noqa: E402
from scitex_cards._messaging import poll_notifications  # noqa: E402

AGENT = "outstanding-poll-agent"


@pytest.fixture
def postgres_mode():
    """Select the shared inbox for one test, then restore the env by hand.

    Mirrors the sibling ``test__inbox_receipt_postgres.py`` fixture: real
    environment variables, set and unset by hand, because the backend resolver
    reads ``os.environ`` and which backend it picks is what this file pins.
    Skips when no writable test DSN is available on this node (CI provides one);
    the hermetic helper layer above does not need it and is not skipped.
    """
    if not _DSN:
        pytest.skip(
            f"${TEST_DSN_ENV} is unset on this node; the integration layer "
            "runs in CI, where a writable throwaway cluster is provided."
        )
    keys = {"SCITEX_CARDS_INBOX_BACKEND": "postgres", "SCITEX_STORE_DSN": _DSN}
    before = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, was in before.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was


def _push_one(store, ts):
    """Enqueue one record and PUSH it — the exact seen=1/confirmed_at=NULL state
    the drain leaves a record in after handing it to a session."""
    record = _inbox.enqueue(
        AGENT,
        event_type="dm",
        card_id="card-1",
        body="a record the session never delivered",
        actor="peer",
        ts=ts,
        store=store,
    )
    record_push(AGENT, [record["id"]], at=ts, store=store)
    return record["id"]


def test_a_pushed_record_is_off_the_default_page(postgres_mode, _DSN):
    # Arrange: one record the drain has pushed (seen, unconfirmed).
    nid = _push_one(_DSN, ts="2026-09-14T06:00:00Z")
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: the default unseen-only page omits it.
    assert nid not in {r.get("id") for r in payload["notifications"]}


def test_a_pushed_record_is_in_outstanding(postgres_mode, _DSN):
    # Arrange: one record the drain has pushed (seen, unconfirmed).
    nid = _push_one(_DSN, ts="2026-09-14T06:00:00Z")
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: outstanding carries it (the full record the page omitted).
    assert [r["id"] for r in payload["outstanding"]] == [nid]


def test_outstanding_ids_equal_unconfirmed_ids(postgres_mode, _DSN):
    # Arrange: two pushed records, one confirmed.
    pushed_a = _push_one(_DSN, ts="2026-09-14T06:00:00Z")
    pushed_b = _push_one(_DSN, ts="2026-09-14T06:01:00Z")
    confirm_notifications(AGENT, [pushed_b], store=_DSN)
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: outstanding's ids are exactly unconfirmed's ids (cannot disagree).
    assert {r["id"] for r in payload["outstanding"]} == set(payload["unconfirmed"])


def test_outstanding_holds_only_the_unconfirmed_record(postgres_mode, _DSN):
    # Arrange: two pushed records, one confirmed.
    pushed_a = _push_one(_DSN, ts="2026-09-14T06:00:00Z")
    pushed_b = _push_one(_DSN, ts="2026-09-14T06:01:00Z")
    confirm_notifications(AGENT, [pushed_b], store=_DSN)
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: only the still-unconfirmed record survives into outstanding.
    assert [r["id"] for r in payload["outstanding"]] == [pushed_a]


def test_outstanding_carries_the_receipt_fields(postgres_mode, _DSN):
    # Arrange: one pushed record.
    nid = _push_one(_DSN, ts="2026-09-14T06:00:00Z")
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: the row is a full record with push stamped, confirm NULL.
    row = next(r for r in payload["outstanding"] if r["id"] == nid)
    assert row["pushed_at"] is not None and row["confirmed_at"] is None


def test_a_record_on_the_page_is_not_duplicated_into_outstanding(postgres_mode, _DSN):
    # Arrange: one record enqueued but NOT pushed (still unseen -> on the page).
    _inbox.enqueue(
        AGENT,
        event_type="commented",
        card_id="card-1",
        body="still unseen",
        actor="peer",
        ts="2026-09-14T07:00:00Z",
        store=_DSN,
    )
    # Act: the recommended poll.
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: page and outstanding are disjoint by id.
    assert {r.get("id") for r in payload["notifications"]}.isdisjoint(
        {r["id"] for r in payload["outstanding"]}
    )


def test_poll_is_read_only_and_repeatable(postgres_mode, _DSN):
    # Arrange: one pushed record.
    nid = _push_one(_DSN, ts="2026-09-14T08:00:00Z")
    # Act: poll twice, read-only.
    first = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    second = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: the cursor did not move — both polls show it in outstanding.
    assert [r["id"] for r in first["outstanding"]] == [
        r["id"] for r in second["outstanding"]
    ] == [nid]


def test_confirming_moves_it_out_of_outstanding(postgres_mode, _DSN):
    # Arrange: one pushed record.
    nid = _push_one(_DSN, ts="2026-09-14T09:00:00Z")
    # Act: confirm it, then poll.
    confirm_notifications(AGENT, [nid], store=_DSN)
    payload = poll_notifications(AGENT, unseen_only=True, store=_DSN)
    # Assert: a real confirmation clears both outstanding and unconfirmed.
    assert payload["outstanding"] == [] and payload["unconfirmed"] == []


# EOF
