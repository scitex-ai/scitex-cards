#!/usr/bin/env python3
"""Lost-update protection for raw read-modify-write cycles.

Pins the fix for incident-bulk-migration-lost-write-race-20260710: a bulk
migration used plain ``load_tasks → mutate → save_tasks`` and silently erased
two concurrent writers' mutations, because nothing tied the write to the read
it was based on. Two primitives close it:

* ``save_tasks(..., expected_generation=...)`` — optimistic concurrency: a
  write based on a stale read is REFUSED (StaleStoreError), never applied.
* ``edit_tasks(path)`` — one lock across the whole cycle, so there is no
  window at all.
"""

from __future__ import annotations

import contextlib
import os

import pytest

from scitex_cards._store_errors import StoreNotProvisionedError
from conftest import seed_db_from_doc

from scitex_cards._model import (
    StaleStoreError,
    edit_tasks,
    load_doc,
    load_tasks,
    save_tasks,
    store_generation,
)


def _seed(tmp_path, n=2):
    """Seed the canonical DB with ``n`` deferred rows; return the STORE path.

    The store is the database now: ``load_tasks`` / ``save_tasks`` read and write the
    canonical database and the ``path`` argument only names which logical store
    is addressed. So seed the DB, then hand back the PINNED store-identity path
    (``SCITEX_CARDS_TASKS_YAML_SHARED``), NOT the DB path — a write stamped with
    any other path is refused by the next read (THE STORE-PATH RULE)."""
    doc = {
        "tasks": [
            {"id": f"t{i}", "title": f"T{i}", "status": "deferred"} for i in range(n)
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _seed_with_users(tmp_path):
    """A store carrying BOTH a ``users:`` registry and a ``tasks:`` list.

    Seeds the canonical DB and returns the pinned STORE path."""
    doc = {
        "users": [{"id": "u1", "kind": "agent", "name": "someone"}],
        "tasks": [{"id": "t0", "title": "T", "status": "deferred"}],
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


#: The lost-update race, staged: writer A reads the world (taking a generation
#: token), then writer B commits a row of its own. A's token is now stale, so
#: A's pending mutation is based on a world that no longer exists. Returns
#: ``(store, tasks_a, gen_a)`` with A's mutation already applied in memory but
#: NOT yet written — the two tests below split what must happen when it is.
def _staged_lost_update(tmp_path):
    store = _seed(tmp_path)
    gen_a = store_generation(store)
    tasks_a = load_tasks(store)

    tasks_b = load_tasks(store)
    tasks_b.append({"id": "from-b", "title": "B's row", "status": "deferred"})
    save_tasks(tasks_b, store)

    tasks_a[0]["priority"] = 1
    return store, tasks_a, gen_a


class TestOptimisticConcurrency:
    def test_stale_write_is_refused(self, tmp_path):
        # Arrange
        store, tasks_a, gen_a = _staged_lost_update(tmp_path)
        # Act
        refusal = pytest.raises(StaleStoreError)
        # Assert — a write based on a vanished world is never applied.
        with refusal:
            save_tasks(tasks_a, store, expected_generation=gen_a)

    def test_refused_stale_write_leaves_the_other_writers_row(self, tmp_path):
        # Arrange
        store, tasks_a, gen_a = _staged_lost_update(tmp_path)
        with contextlib.suppress(StaleStoreError):
            save_tasks(tasks_a, store, expected_generation=gen_a)
        # Act
        surviving = load_tasks(store)
        # Assert — B's row is what the refusal protected.
        assert any(t["id"] == "from-b" for t in surviving)

    def test_fresh_write_passes(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        gen = store_generation(store)
        tasks = load_tasks(store)
        tasks[0]["priority"] = 1
        # Act
        save_tasks(tasks, store, expected_generation=gen)
        # Assert — nothing intervened, so the token still matched.
        assert load_tasks(store)[0]["priority"] == 1

    def test_without_token_behaviour_is_unchanged(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        tasks = load_tasks(store)
        tasks[0]["priority"] = 3
        # Act
        save_tasks(tasks, store)
        # Assert — the token is opt-in; existing callers keep working.
        assert load_tasks(store)[0]["priority"] == 3

    def test_generation_of_missing_store(self, env, new_store):
        # Arrange — the store IS the canonical DB, and store_generation hashes
        # THAT (ignoring the path arg), so the "store absent" case the sentinel
        # is for is a store with nothing in it. `os.remove` on $SCITEX_CARDS_DB
        # was how that used to be arranged; the variable holds a DSN now, so
        # removing it raised FileNotFoundError before the assertion ran. The
        # comment above it already said the pinned value "never was a real file
        # once the store became a database" — the arrangement just had not
        # caught up.
        #
        # `bootstrap=False` is the modern absent store: reachable, but carrying
        # no schema, which is what a fresh deployment looks like.
        #
        # AND THE ANSWER INVERTED. This asserted "a stable sentinel, not a
        # raise". An unprovisioned store now RAISES, on purpose:
        #
        #     StoreNotProvisionedError: the PostgreSQL store ... has no `tasks`
        #     table. REFUSING to continue: the exporter answers a schemaless
        #     database with an empty document, and that value is written back
        #     as the WHOLE store.
        #
        # That is the same class of bug the sentinel was protecting against,
        # caught one layer earlier and more loudly. A quiet token for an absent
        # store is exactly what let an empty document look like a legitimate
        # generation. So the test keeps its subject — what happens when the
        # store is not there — and follows the behaviour to its refusal.
        env.set("SCITEX_CARDS_DB", new_store("concurrency_absent", bootstrap=False))

        # Act
        def read_the_generation():
            return store_generation(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])

        # Assert
        with pytest.raises(StoreNotProvisionedError):
            read_the_generation()


class TestEditTasks:
    def test_mutation_persists_on_clean_exit(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        # Act
        with edit_tasks(store) as tasks:
            for t in tasks:
                t["priority"] = 2
        # Assert
        assert all(t["priority"] == 2 for t in load_tasks(store))

    def test_body_exception_propagates_to_the_caller(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        # Act
        propagated = pytest.raises(RuntimeError, match="boom")
        # Assert — the cycle never swallows the caller's failure.
        with propagated:
            with edit_tasks(store) as tasks:
                tasks[0]["priority"] = 9
                raise RuntimeError("boom")

    def test_nothing_written_on_exception(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        # Act — an edit that raises mid-cycle must persist nothing.
        with contextlib.suppress(RuntimeError):
            with edit_tasks(store) as tasks:
                tasks[0]["priority"] = 9
                raise RuntimeError("boom")
        # Assert — the half-done mutation never reached the store. Read the
        # persisted DATA back rather than compare store_generation() before and
        # after: the store used write-ahead logging, where even a read rewrites the
        # main DB file, so the content token is not read-stable and cannot
        # witness "unchanged" — the rows can, and are the actual subject.
        assert load_tasks(store)[0].get("priority") is None

    def test_preserves_non_tasks_sections(self, tmp_path):
        # Arrange
        store = _seed_with_users(tmp_path)
        # Act
        with edit_tasks(store) as tasks:
            tasks[0]["priority"] = 1
        # Assert — the users: registry survives a tasks-only edit. Store is
        # Read the users section back from the canonical DB instead of a
        # YAML file's text — the rule is unchanged, only its serialization is gone.
        assert any(u.get("id") == "u1" for u in load_doc(store).get("users", []))

    def test_tasks_only_edit_still_persists_the_mutation(self, tmp_path):
        # Arrange
        store = _seed_with_users(tmp_path)
        # Act
        with edit_tasks(store) as tasks:
            tasks[0]["priority"] = 1
        # Assert — preserving users: must not cost the edit itself.
        assert load_tasks(store)[0]["priority"] == 1

    def test_append_inside_cycle(self, tmp_path):
        # Arrange
        store = _seed(tmp_path, n=1)
        # Act
        with edit_tasks(store) as tasks:
            tasks.append({"id": "new", "title": "added", "status": "deferred"})
        # Assert — the migration shape: read all, add rows, write back.
        assert {t["id"] for t in load_tasks(store)} == {"t0", "new"}
