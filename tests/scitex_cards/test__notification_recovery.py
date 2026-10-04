"""Recovery protocol and parameterized-query controls; these are not PG proof.

The actual private core uses explicit Rows; the canonical adapter uses a typed
DB-API collaborator. No global/module functions are replaced. Real PostgreSQL
execution remains a separate owned-fixture/CI qualification.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from scitex_cards._inbox_record import NOTIFICATION_RECORD_KEYS
from scitex_cards._notification_recovery import (
    _COLUMNS,
    _PostgresRows,
    _lookup_query,
    _page_query,
    _recover_with_rows,
    _validate_selectors,
)


def _row(nid, *, recipient="raw-agent", seq=1, seen=False, confirmed=None):
    return {
        "id": nid, "recipient": recipient, "seq": seq,
        "event_type": "dm", "card_id": "dm:operator::raw-agent",
        "body": "original\n日本語 💌", "actor": "operator", "ts": "t0",
        "seen": seen, "msg_id": "m_original", "exchange_id": "xch_original",
        "pushed_at": "p0", "confirmed_at": confirmed,
    }


class _ProtocolRows:
    def __init__(self, records):
        self.records = deepcopy(records)
        self.lookups = []
        self.pages = []

    def lookup(self, keys, notification_id):
        self.lookups.append((list(keys), notification_id))
        return next((deepcopy(r) for r in self.records
                     if r["recipient"] in keys and r["id"] == notification_id), None)

    def page(self, keys, unseen_only, limit, cursor):
        self.pages.append((list(keys), unseen_only, limit, deepcopy(cursor)))
        records = [r for r in self.records
                   if r["recipient"] in keys and not (unseen_only and r["seen"])]
        if cursor is not None:
            def beyond(row):
                if cursor["seq"] is None:
                    return row["seq"] is None and row["id"] > cursor["id"]
                return row["seq"] is None or (row["seq"], row["id"]) > (
                    cursor["seq"], cursor["id"])
            records = [r for r in records if beyond(r)]
        records.sort(key=lambda r: (r["seq"] is None, r["seq"] or 0, r["id"]))
        return deepcopy(records[:limit + 1])


def _recover(rows, *, nid=None, limit=2, after=None, unseen_only=False):
    return _recover_with_rows(
        rows, agent="raw-agent", keys=["raw-agent", "u_stable"],
        store="declared-test-store", unseen_only=unseen_only,
        notification_id=nid, limit=limit, after=after,
    )


@pytest.mark.parametrize("value", [None, "", "original\n日本語 💌"])
def test_exact_read_preserves_original_payload_and_receipts(value):
    original = _row("n_original", seen=1)
    for key in ("body", "actor", "msg_id", "exchange_id"):
        original[key] = value
    rows = _ProtocolRows([original])
    result = _recover(rows, nid="n_original", limit=1)
    expected = {key: original[key] for key in _COLUMNS if key != "seq"}
    expected["seen"] = True
    assert result["notifications"] == [expected]
    assert result["unconfirmed"] == ["n_original"]
    assert result["unconfirmed_scope"] == "page"
    assert result["outstanding"] == []
    assert rows.records == [original]
    assert rows.lookups == [(["raw-agent", "u_stable"], "n_original")]
    assert rows.pages == []


def test_page_combines_recipient_keys_before_global_limit():
    rows = _ProtocolRows([
        _row("n_raw_late", seq=7),
        _row("n_stable_first", recipient="u_stable", seq=0),
        _row("n_foreign", recipient="somebody-else", seq=-1),
        _row("n_raw_middle", seq=3),
    ])
    result = _recover(rows)
    assert [r["id"] for r in result["notifications"]] == [
        "n_stable_first", "n_raw_middle"
    ]
    assert result["page"]["next_cursor"] == "n_raw_middle"
    assert len(rows.pages) == 1
    assert rows.pages[0][:3] == (["raw-agent", "u_stable"], False, 2)


def test_history_pages_keep_zero_sequences_and_all_null_sequence_rows():
    original = [
        _row("n_zero", seq=0), _row("n_tie_b", seq=2), _row("n_tie_a", seq=2),
        _row("n_null_b", seq=None, recipient="u_stable"),
        _row("n_null_a", seq=None), _row("n_null_c", seq=None),
    ]
    rows = _ProtocolRows(original)
    ids = []
    after = None
    for _ in range(4):
        page = _recover(rows, limit=2, after=after)
        ids.extend(r["id"] for r in page["notifications"])
        after = page["page"]["next_cursor"]
        if after is None:
            break
    assert ids == ["n_zero", "n_tie_a", "n_tie_b", "n_null_a", "n_null_b", "n_null_c"]
    assert len(rows.pages) == 3
    assert rows.records == original


def test_cursor_survives_seen_and_confirmation_filters():
    rows = _ProtocolRows([
        _row("n_cursor", seen=True, confirmed="c0"), _row("n_next", seq=2),
    ])
    result = _recover(rows, after="n_cursor", unseen_only=True)
    assert [r["id"] for r in result["notifications"]] == ["n_next"]
    assert rows.lookups[0][1] == "n_cursor"


def test_exact_seen_history_requires_explicit_unseen_false():
    rows = _ProtocolRows([_row("n_seen", seen=True)])
    assert _recover(rows, nid="n_seen", unseen_only=True)["notifications"] == []
    result = _recover(rows, nid="n_seen", unseen_only=False)
    assert result["notifications"][0]["id"] == "n_seen"


def test_unknown_and_foreign_exact_ids_have_identical_empty_results():
    rows = _ProtocolRows([_row("n_foreign", recipient="somebody-else")])
    assert _recover(rows, nid="n_foreign") == _recover(rows, nid="n_unknown")


def test_unknown_and_foreign_cursors_refuse_before_page_read():
    rows = _ProtocolRows([_row("n_foreign", recipient="somebody-else")])
    errors = []
    for cursor in ("n_foreign", "n_unknown"):
        with pytest.raises(ValueError) as error:
            _recover(rows, after=cursor)
        errors.append(str(error.value))
    assert errors == ["invalid notification cursor for this recipient"] * 2
    assert rows.pages == []


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_next_cursor_requires_actual_extra_row(count):
    rows = _ProtocolRows([_row(f"n_{i}", seq=i) for i in range(count)])
    result = _recover(rows, limit=2)
    assert len(result["notifications"]) == min(count, 2)
    assert result["page"]["next_cursor"] == ("n_1" if count > 2 else None)


@pytest.mark.parametrize("confirmed", [None, "", "c0"])
def test_confirmation_semantics_preserve_null_empty_and_nonempty_stamps(confirmed):
    rows = _ProtocolRows([_row("n_confirmed", confirmed=confirmed)])
    result = _recover(rows, nid="n_confirmed")
    assert result["notifications"][0]["confirmed_at"] == confirmed
    assert result["unconfirmed"] == (["n_confirmed"] if not confirmed else [])
    assert result["confirm_with"] == "ack_notifications"


@pytest.mark.parametrize("changes", [
    {"agent": ""}, {"agent": None}, {"ack": True}, {"ack": 1},
    {"unseen_only": 1}, {"limit": True}, {"limit": 0}, {"limit": 101},
    {"limit": 1.5}, {"limit": "2"}, {"notification_id": ""},
    {"notification_id": 12}, {"notification_id": "bad\0id"},
    {"notification_id": "n_one", "limit": 1},
    {"notification_id": "n_one", "after": "n_other"}, {"after": ""},
])
def test_invalid_selectors_refuse_at_actual_local_boundary(changes):
    from scitex_cards._backend import LocalBackend

    arguments = {"agent": "raw-agent", "unseen_only": False, "ack": False,
                 "notification_id": None, "limit": 2, "after": None}
    arguments.update(changes)
    with pytest.raises(ValueError):
        LocalBackend().poll_notifications(**arguments)


def test_defaults_and_maximum_are_explicit():
    assert _validate_selectors("a", False, False, None, None, "n_cursor") == 50
    assert _validate_selectors("a", False, False, None, 100, None) == 100
    assert _validate_selectors("a", False, False, "n_id", None, None) == 1


@pytest.mark.parametrize(
    "cursor", [None, _row("n_zero", seq=0), _row("n_old", seq=None)]
)
def test_page_query_is_recipient_scoped_and_globally_bounded(cursor):
    query, params = _page_query(["raw-agent", "u_stable"], False, 100, cursor)
    assert "n.recipient_id = ANY(%s)" in query
    assert "ORDER BY n.seq ASC NULLS LAST, n.id ASC LIMIT %s" in query
    assert params[0] == ["raw-agent", "u_stable"]
    assert params[-1] == 101
    assert "OFFSET" not in query
    assert not any(
        word in query for word in ("UPDATE", "INSERT", "DELETE", "record_json")
    )
    if cursor is not None and cursor["seq"] is None:
        assert "AND n.seq IS NULL AND n.id > %s" in query
        assert params[1] == "n_old"
    elif cursor is not None:
        assert "OR n.seq IS NULL" in query
        assert params[1:4] == (0, 0, "n_zero")


def test_id_and_cursor_are_bound_values_never_sql_text():
    value = "n_' OR recipient_id <> 'mine"
    query, params = _lookup_query(["raw-agent"], value)
    assert value not in query and params == (["raw-agent"], value)
    assert query.endswith("LIMIT 1")
    query, params = _page_query(["raw-agent"], True, 3, _row(value, seq=None))
    assert value not in query and value in params
    assert "n.seen = 0" in query


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.connection.queries.append((query, params))

    def fetchall(self):
        return self.connection.responses.pop(0)


class _Connection:
    def __init__(self, responses):
        self.responses = responses
        self.queries = []

    def cursor(self):
        return _Cursor(self)


@pytest.mark.parametrize("as_mapping", [False, True])
def test_real_canonical_adapter_executes_scoped_selects_with_driver_row_shapes(
    as_mapping,
):
    record = _row("n_original", seq=None)
    raw = {key: record[key] for key in _COLUMNS}
    response = raw if as_mapping else tuple(raw.values())
    connection = _Connection([[response], [response]])
    adapter = _PostgresRows(connection)
    found = adapter.lookup(["raw-agent", "u_stable"], "n_original")
    assert found == raw
    assert adapter.page(["raw-agent", "u_stable"], False, 2, None) == [raw]
    assert connection.queries == [
        _lookup_query(["raw-agent", "u_stable"], "n_original"),
        _page_query(["raw-agent", "u_stable"], False, 2, None),
    ]
    assert connection.responses == []


def test_receipt_projection_contains_every_original_notification_field():
    from scitex_cards._inbox_receipt_postgres import _READ_COLUMNS

    assert _READ_COLUMNS == NOTIFICATION_RECORD_KEYS + ("pushed_at", "confirmed_at")
