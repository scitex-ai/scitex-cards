"""Real PostgreSQL clients and scheduling instruments for Cards row races.

Every query/cursor/row is delegated to the production psycopg adapter. The
instrument pauses a real client at an observable boundary; it never supplies a
row, SQL result, row count or authorization decision.
"""

import json
import threading
import time
from contextlib import contextmanager
from copy import deepcopy

from scitex_cards._db import connect
from scitex_cards._db_bootstrap import _insert_tasks
from scitex_cards._mirror_hashes import _HASH_DDL, HASH_TABLE
from scitex_cards._mirror_rows import _write_card


def card(title="initial"):
    return {
        "id": "alpha",
        "title": title,
        "status": "deferred",
        "assignee": "agent:test-suite",
        "comments": [
            {"author": "fixture", "ts": "2026-10-03T00:00:00Z", "text": title}
        ],
        "collaborators": ["fixture:" + title],
        "blocks": ["beta"],
    }


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


@contextmanager
def connection(dsn):
    conn = connect(dsn)
    try:
        conn.execute("SET statement_timeout = '10000ms'")
        conn.execute("SET lock_timeout = '8000ms'")
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def seed(dsn, *, present=True):
    with connection(dsn) as conn:
        _insert_tasks(conn, [{"id": "beta", "title": "peer", "status": "deferred"}])
        if present:
            _insert_tasks(conn, [card()])
        conn.execute(_HASH_DDL)
        if present:
            conn.execute(
                f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)",
                ("alpha", "initial-ledger"),
            )
        conn.commit()


def snapshot(dsn):
    with connection(dsn) as conn:
        row = conn.execute(
            "SELECT card_json, revision FROM tasks WHERE id = ?", ("alpha",)
        ).fetchone()
        return {
            "parent": None if row is None else dict(row),
            "comments": [
                dict(r)
                for r in conn.execute(
                    "SELECT seq, text FROM task_comments "
                    "WHERE task_id = ? ORDER BY seq",
                    ("alpha",),
                ).fetchall()
            ],
            "roles": [
                dict(r)
                for r in conn.execute(
                    "SELECT who, role FROM task_roles "
                    "WHERE task_id = ? ORDER BY who, role",
                    ("alpha",),
                ).fetchall()
            ],
            "edges": [
                dict(r)
                for r in conn.execute(
                    "SELECT dst_task_id, edge_type FROM task_edges "
                    "WHERE src_task_id = ? ORDER BY dst_task_id, edge_type",
                    ("alpha",),
                ).fetchall()
            ],
            "hash": conn.execute(
                f"SELECT hash FROM {HASH_TABLE} WHERE task_id = ?", ("alpha",)
            ).fetchone(),
        }


def enroll(conn, *, absent=False):
    # Fixture-only producer state, inside the owned schema. This does not call
    # or impersonate an authenticated delivery operation/actor.
    value = card("enrolled")
    value["_log_meta"] = {"delivery": {"fixture_generation": "owned-enrollment"}}
    if absent:
        _insert_tasks(conn, [value])
    else:
        conn.execute(
            "UPDATE tasks SET card_json = ?, log_meta_json = ? WHERE id = ?",
            (json.dumps(value), json.dumps(value["_log_meta"]), "alpha"),
        )
    conn.commit()
    return value


class PausedCursor:
    def __init__(self, cursor, owner):
        self.cursor = cursor
        self.owner = owner

    def fetchone(self):
        row = self.cursor.fetchone()
        self.owner.pause()
        return row

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class BoundaryConnection:
    def __init__(self, conn, *, boundary="canonical-read"):
        self.conn = conn
        self.boundary = boundary
        self.reached = threading.Event()
        self.release = threading.Event()
        self.paused = False
        self.locked_read = False

    def pause(self):
        self.paused = True
        self.reached.set()
        require(self.release.wait(8), "real-client-boundary-timeout")

    def execute(self, sql, params=()):
        if (
            not self.paused
            and self.boundary == "parent-mutation"
            and (
                sql.startswith("INSERT INTO tasks ")
                or sql.startswith("UPDATE tasks SET ")
            )
        ):
            self.pause()
        cursor = self.conn.execute(sql, params)
        if (
            not self.paused
            and self.boundary == "canonical-read"
            and sql.startswith(
                "SELECT card_json, revision, last_activity FROM tasks WHERE id = ?"
            )
        ):
            self.locked_read = sql.endswith(" FOR UPDATE")
            return PausedCursor(cursor, self)
        return cursor

    def __getattr__(self, name):
        return getattr(self.conn, name)


class RealClient:
    def __init__(self, dsn, operation, *, boundary="canonical-read"):
        self.dsn = dsn
        self.operation = operation
        self.boundary = boundary
        self.ready = threading.Event()
        self.done = threading.Event()
        self.pid = None
        self.instrument = None
        self.outcome = None
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with connection(self.dsn) as conn:
                self.pid = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()[
                    "pid"
                ]
                self.instrument = BoundaryConnection(conn, boundary=self.boundary)
                self.ready.set()
                try:
                    self.outcome = self.operation(self.instrument)
                    conn.commit()
                except Exception as error:
                    conn.rollback()
                    self.error = type(error).__name__
        except Exception as error:
            self.error = type(error).__name__
        finally:
            self.ready.set()
            self.done.set()

    def start(self):
        self.thread.start()
        require(
            self.ready.wait(5) and self.instrument is not None,
            "real-client-connection-refused",
        )
        require(self.instrument.reached.wait(5), "real-client-boundary-not-reached")
        return self

    def finish(self):
        if self.instrument is not None:
            self.instrument.release.set()
        self.thread.join(12)
        require(not self.thread.is_alive(), "real-client-did-not-finish")


@contextmanager
def paused_client(
    dsn, operation, record_property, *, boundary="canonical-read", allowed_errors=()
):
    client = RealClient(dsn, operation, boundary=boundary)
    try:
        client.start()
        record_property("writer_backend_pid", client.pid)
        yield client
    finally:
        client.finish()
        require(
            client.error is None or client.error in allowed_errors,
            "writer-client-unexpected-failure",
        )


def winner(dsn, revision):
    with connection(dsn) as conn:
        _write_card(conn, deepcopy(card("winner")), expected_revision=revision)
        conn.execute(
            f"UPDATE {HASH_TABLE} SET hash = ? WHERE task_id = ?",
            ("winner-ledger", "alpha"),
        )
        conn.commit()


def enrollment_while_paused(dsn, client, record_property):
    done = threading.Event()
    state = {"pid": None, "error": None}

    def run():
        try:
            with connection(dsn) as conn:
                state["pid"] = conn.execute(
                    "SELECT pg_backend_pid() AS pid"
                ).fetchone()["pid"]
                enroll(conn)
        except Exception as error:
            state["error"] = type(error).__name__
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    blocked = False
    try:
        with connection(dsn) as observer:
            deadline = time.monotonic() + 5
            while not done.is_set() and time.monotonic() < deadline:
                if state["pid"] is not None:
                    blockers = observer.execute(
                        "SELECT pg_blocking_pids(?) AS blockers", (state["pid"],)
                    ).fetchone()["blockers"]
                    if client.pid in blockers:
                        blocked = True
                        break
                time.sleep(0.01)
        require(done.is_set() or blocked, "contender-neither-finished-nor-blocked")
        if client.instrument.locked_read:
            require(blocked, "for-update-lock-did-not-fence-contender")
        record_property("contender_backend_pid", state["pid"])
        record_property("actual_parent_lock_observed", blocked)
        require(state["pid"] != client.pid, "race-needs-two-real-backends")
    finally:
        client.finish()
        thread.join(12)
    require(
        not thread.is_alive() and state["error"] is None, "enrollment-client-failed"
    )
