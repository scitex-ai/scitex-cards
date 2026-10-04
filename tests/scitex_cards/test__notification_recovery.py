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
    # Arrange
    original = _row("n_original", seen=1)
    for key in ("body", "actor", "msg_id", "exchange_id"):
        original[key] = value
    rows = _ProtocolRows([original])
    expected = {key: original[key] for key in _COLUMNS if key != "seq"}
    expected["seen"] = True
    # Act
    result = _recover(rows, nid="n_original", limit=1)
    # Assert
    assert (
        result["notifications"] == [expected],
        result["unconfirmed"] == ["n_original"],
        result["unconfirmed_scope"] == "page",
        result["outstanding"] == [],
        rows.records == [original],
        rows.lookups == [(["raw-agent", "u_stable"], "n_original")],
        rows.pages == [],
    ) == (True, True, True, True, True, True, True)


def test_page_combines_recipient_keys_before_global_limit():
    # Arrange
    rows = _ProtocolRows([
        _row("n_raw_late", seq=7),
        _row("n_stable_first", recipient="u_stable", seq=0),
        _row("n_foreign", recipient="somebody-else", seq=-1),
        _row("n_raw_middle", seq=3),
    ])
    # Act
    result = _recover(rows)
    # Assert
    assert (
        [r["id"] for r in result["notifications"]] == [
            "n_stable_first", "n_raw_middle"
        ],
        result["page"]["next_cursor"] == "n_raw_middle",
        len(rows.pages) == 1,
        rows.pages[0][:3] == (["raw-agent", "u_stable"], False, 2),
    ) == (True, True, True, True)


def test_history_pages_keep_zero_sequences_and_all_null_sequence_rows():
    # Arrange
    original = [
        _row("n_zero", seq=0), _row("n_tie_b", seq=2), _row("n_tie_a", seq=2),
        _row("n_null_b", seq=None, recipient="u_stable"),
        _row("n_null_a", seq=None), _row("n_null_c", seq=None),
    ]
    rows = _ProtocolRows(original)
    ids = []
    after = None
    # Act
    for _ in range(4):
        page = _recover(rows, limit=2, after=after)
        ids.extend(r["id"] for r in page["notifications"])
        after = page["page"]["next_cursor"]
        if after is None:
            break
    # Assert
    assert (
        ids == ["n_zero", "n_tie_a", "n_tie_b", "n_null_a", "n_null_b", "n_null_c"],
        len(rows.pages) == 3,
        rows.records == original,
    ) == (True, True, True)


def test_cursor_survives_seen_and_confirmation_filters():
    # Arrange
    rows = _ProtocolRows([
        _row("n_cursor", seen=True, confirmed="c0"), _row("n_next", seq=2),
    ])
    # Act
    result = _recover(rows, after="n_cursor", unseen_only=True)
    # Assert
    assert (
        [r["id"] for r in result["notifications"]] == ["n_next"],
        rows.lookups[0][1] == "n_cursor",
    ) == (True, True)


def test_exact_seen_history_requires_explicit_unseen_false():
    # Arrange
    rows = _ProtocolRows([_row("n_seen", seen=True)])
    # Act
    unseen_is_empty = _recover(rows, nid="n_seen", unseen_only=True)["notifications"] == []
    result = _recover(rows, nid="n_seen", unseen_only=False)
    # Assert
    assert (unseen_is_empty, result["notifications"][0]["id"] == "n_seen") == (
        True, True,
    )


def test_unknown_and_foreign_exact_ids_have_identical_empty_results():
    # Arrange
    rows = _ProtocolRows([_row("n_foreign", recipient="somebody-else")])
    # Act
    foreign = _recover(rows, nid="n_foreign")
    unknown = _recover(rows, nid="n_unknown")
    # Assert
    assert foreign == unknown


def test_unknown_and_foreign_cursors_refuse_before_page_read():
    # Arrange
    rows = _ProtocolRows([_row("n_foreign", recipient="somebody-else")])
    errors = []
    # Act
    for cursor in ("n_foreign", "n_unknown"):
        try:
            _recover(rows, after=cursor)
        except ValueError as error:
            errors.append(str(error))
        else:
            errors.append(None)
    # Assert
    assert (
        errors == ["invalid notification cursor for this recipient"] * 2,
        rows.pages == [],
    ) == (True, True)


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_next_cursor_requires_actual_extra_row(count):
    # Arrange
    rows = _ProtocolRows([_row(f"n_{i}", seq=i) for i in range(count)])
    # Act
    result = _recover(rows, limit=2)
    # Assert
    assert (
        len(result["notifications"]) == min(count, 2),
        result["page"]["next_cursor"] == ("n_1" if count > 2 else None),
    ) == (True, True)


@pytest.mark.parametrize("confirmed", [None, "", "c0"])
def test_confirmation_semantics_preserve_null_empty_and_nonempty_stamps(confirmed):
    # Arrange
    rows = _ProtocolRows([_row("n_confirmed", confirmed=confirmed)])
    # Act
    result = _recover(rows, nid="n_confirmed")
    # Assert
    assert (
        result["notifications"][0]["confirmed_at"] == confirmed,
        result["unconfirmed"] == (["n_confirmed"] if not confirmed else []),
        result["confirm_with"] == "ack_notifications",
    ) == (True, True, True)


@pytest.mark.parametrize("changes", [
    {"agent": ""}, {"agent": None}, {"ack": True}, {"ack": 1},
    {"unseen_only": 1}, {"limit": True}, {"limit": 0}, {"limit": 101},
    {"limit": 1.5}, {"limit": "2"}, {"notification_id": ""},
    {"notification_id": 12}, {"notification_id": "bad\0id"},
    {"notification_id": "n_one", "limit": 1},
    {"notification_id": "n_one", "after": "n_other"}, {"after": ""},
])
def test_invalid_selectors_refuse_at_actual_local_boundary(changes):
    # Arrange
    from scitex_cards._backend import LocalBackend

    arguments = {"agent": "raw-agent", "unseen_only": False, "ack": False,
                 "notification_id": None, "limit": 2, "after": None}
    arguments.update(changes)
    # Act
    # Assert
    with pytest.raises(ValueError):
        LocalBackend().poll_notifications(**arguments)


def test_defaults_and_maximum_are_explicit():
    # Arrange
    # Act
    defaults = (
        _validate_selectors("a", False, False, None, None, "n_cursor") == 50,
        _validate_selectors("a", False, False, None, 100, None) == 100,
        _validate_selectors("a", False, False, "n_id", None, None) == 1,
    )
    # Assert
    assert defaults == (True, True, True)


@pytest.mark.parametrize(
    "cursor, expected_fragment, expected_cursor_params", [
        (None, "n.recipient_id = ANY(%s)", ()),
        (_row("n_zero", seq=0), "OR n.seq IS NULL", (0, 0, "n_zero")),
        (_row("n_old", seq=None), "AND n.seq IS NULL AND n.id > %s", ("n_old",)),
    ],
)
def test_page_query_is_recipient_scoped_and_globally_bounded(
    cursor, expected_fragment, expected_cursor_params,
):
    # Arrange
    # Act
    query, params = _page_query(["raw-agent", "u_stable"], False, 100, cursor)
    # Assert
    assert (
        "n.recipient_id = ANY(%s)" in query,
        "ORDER BY n.seq ASC NULLS LAST, n.id ASC LIMIT %s" in query,
        params[0] == ["raw-agent", "u_stable"],
        params[-1] == 101,
        "OFFSET" not in query,
        not any(word in query for word in ("UPDATE", "INSERT", "DELETE", "record_json")),
        expected_fragment in query,
        params[1:-1] == expected_cursor_params,
    ) == (True, True, True, True, True, True, True, True)


def test_id_and_cursor_are_bound_values_never_sql_text():
    # Arrange
    value = "n_' OR recipient_id <> 'mine"
    # Act
    query, params = _lookup_query(["raw-agent"], value)
    lookup_checks = (
        value not in query and params == (["raw-agent"], value),
        query.endswith("LIMIT 1"),
    )
    query, params = _page_query(["raw-agent"], True, 3, _row(value, seq=None))
    # Assert
    assert (
        *lookup_checks,
        value not in query and value in params,
        "n.seen = 0" in query,
    ) == (True, True, True, True)


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
    # Arrange
    record = _row("n_original", seq=None)
    raw = {key: record[key] for key in _COLUMNS}
    response = raw if as_mapping else tuple(raw.values())
    connection = _Connection([[response], [response]])
    adapter = _PostgresRows(connection)
    # Act
    found = adapter.lookup(["raw-agent", "u_stable"], "n_original")
    found_matches = found == raw
    page_matches = adapter.page(["raw-agent", "u_stable"], False, 2, None) == [raw]
    # Assert
    assert (
        found_matches,
        page_matches,
        connection.queries == [
            _lookup_query(["raw-agent", "u_stable"], "n_original"),
            _page_query(["raw-agent", "u_stable"], False, 2, None),
        ],
        connection.responses == [],
    ) == (True, True, True, True)


def test_receipt_projection_contains_every_original_notification_field():
    # Arrange
    from scitex_cards._inbox_receipt_postgres import _READ_COLUMNS

    # Act
    columns = _READ_COLUMNS
    # Assert
    assert columns == NOTIFICATION_RECORD_KEYS + ("pushed_at", "confirmed_at")
