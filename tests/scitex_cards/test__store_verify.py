#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit + integration coverage for the cheap post-dump store-write verify.

Fix B2 replaced the full ``safe_load`` construct-reparse in
``_save_doc_unlocked`` (which cost ~2.3 s / ~159k objects on the live 9.2 MB
store) with a two-part cheap integrity check in
``_store_verify._verify_dumped_tmp``: a byte-length check + a libyaml
EVENT-SCAN reparse to ``StreamEndEvent``. This module pins scitex-dev's
THREE non-negotiables for that change:

  (a) A truncation-MID-SCALAR corruption that is the SAME byte length as a
      valid dump: the length check PASSES yet the event-scan CATCHES it —
      proving the event-scan is load-bearing, not redundant with length.
  (b) The 2026-06-13 guard intent: a tmp truncated mid-document (the incident
      shape — file ended mid-string) RAISES, i.e. the check proves the bytes
      are FULLY reparseable (reach StreamEnd), not merely non-empty.
  (c) The event-scan MUST reach stream-end, not stop at the first event: a
      valid (large / multi-doc) file is accepted only after full consumption;
      a file whose first events parse fine but then hits EOF mid-stream RAISES.

Plus the happy path (a normal valid write verifies AND promotes via
os.replace) and the byte-length short-write guard.

Real tmp files, NO mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import os
import time

import pytest
import yaml

from scitex_cards import _model
from scitex_cards._store_verify import StoreWriteVerifyError, _verify_dumped_tmp
from scitex_cards._yaml import _SAFE_LOADER, safe_dump


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _valid_doc(n: int = 1) -> dict:
    """A structurally valid store doc with ``n`` tasks."""
    return {
        "tasks": [
            {"id": f"t{i}", "title": f"Title {i}", "status": "pending"}
            for i in range(n)
        ],
        # The store is SQLite now: the `users:` section is a LIST of records
        # each carrying its own `id` (the DB-canonical shape), not the old YAML
        # dict-map `{name: {...}}` (which the write path silently drops). `kind`
        # is the one NOT-NULL column beyond the id PK.
        "users": [{"id": "alice", "kind": "human", "role": "dev"}],
    }


def _write(path, text: str) -> None:
    """Write raw text to ``path`` (byte-exact, utf-8)."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)


def _tmp_holding(tmp_path, text: str):
    """A ``.tasks.yaml.tmp`` sidecar holding exactly ``text``."""
    tmp = tmp_path / ".tasks.yaml.tmp"
    _write(tmp, text)
    return tmp


def _verify_error_message(tmp, dumped: str) -> str:
    """The text ``_verify_dumped_tmp`` rejects ``tmp`` with.

    Lets a test pin ONE property of the message without re-counting the
    raise itself as a second assertion.
    """
    with pytest.raises(StoreWriteVerifyError) as excinfo:
        _verify_dumped_tmp(tmp, dumped)
    return str(excinfo.value)


def _events_before_failure(path):
    """Parse ``path`` until the stream breaks; return the events consumed.

    The parse MUST fail — that expectation is asserted here, so every test
    built on this helper carries it without repeating it.
    """
    events = []
    with pytest.raises(yaml.YAMLError):
        with path.open(encoding="utf-8") as fh:
            for event in yaml.parse(fh, Loader=_SAFE_LOADER):
                events.append(event)
    return events


#: A valid dump whose LAST scalar is ``status: pending``, paired with a
#: byte-for-byte SAME-LENGTH corruption of it: the first character of that
#: scalar's value is replaced by a double-quote, opening a quoted scalar that
#: is never closed before EOF. That is the 2026-06-13 incident shape at an
#: IDENTICAL length — so the byte-length check passes and only the event-scan
#: can catch it. The tests in ``TestSameLengthMidScalarCorruption`` each pin
#: one link of that argument; dropping any of them leaves the claim
#: "the event-scan is load-bearing" unproven.
GOOD_DUMP = safe_dump({"tasks": [{"id": "a", "title": "A", "status": "pending"}]})
BAD_SAME_LENGTH = GOOD_DUMP.replace("status: pending", 'status: "ending', 1)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.fixture()
def promoted_store(env):
    """A 3-task doc written end-to-end through the REAL save path.

    Yields the canonical STORE path after ``_save_doc_unlocked`` committed the
    doc to SQLite (the canonical slot now — there is no YAML file to
    os.replace), so each ``TestHappyPath`` test below pins one property of that
    single completed write instead of re-running it.

    The write MUST address the pinned STORE identity
    (``SCITEX_CARDS_TASKS_YAML_SHARED`` == ``resolve_tasks_path(None)``): a
    write stamps the canonical DB with the path it was handed, and the next
    read refuses the DB unless that stamp equals the resolved store. Handing a
    throwaway ``tmp_path`` here would stamp the DB for a store nothing reads,
    so every round-trip below would raise "stamped for a DIFFERENT store".
    """
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    with _model._store_lock(store):
        _model._save_doc_unlocked(_valid_doc(3), store)
    return store


class TestHappyPath:
    def test_valid_dump_verifies_without_raising(self, tmp_path):
        # Arrange
        dumped = safe_dump(_valid_doc(5))
        tmp = _tmp_holding(tmp_path, dumped)
        # Act
        out = _verify_dumped_tmp(tmp, dumped)
        # Assert — returns None, does not raise.
        assert out is None

    def test_save_doc_unlocked_persists_into_the_canonical_store(self, promoted_store):
        """End-to-end: the write path still commits a valid doc into the
        canonical slot. SQLite is that slot now (there is no YAML file to
        os.replace, and no ``.tmp`` sidecar to promote), so 'promoted' means
        the doc is durably readable back from the canonical store."""
        # Arrange
        store = promoted_store
        # Act
        reloaded = _model.load_doc(store)
        # Assert
        assert len(reloaded["tasks"]) == 3

    def test_save_doc_unlocked_round_trips_every_task(self, promoted_store):
        # Arrange
        expected = {"t0", "t1", "t2"}
        # Act
        reloaded = _model.load_doc(promoted_store)
        # Assert
        assert {t["id"] for t in reloaded["tasks"]} == expected

    def test_save_doc_unlocked_round_trips_the_users_section(self, promoted_store):
        # Arrange — the non-tasks ``users:`` section still survives the write
        # untouched; it round-trips as the DB's canonical list-of-records shape
        # (each record carries its own ``id``), not the old YAML dict-map.
        expected = [{"id": "alice", "kind": "human", "role": "dev"}]
        # Act
        reloaded = _model.load_doc(promoted_store)
        # Assert
        assert reloaded["users"] == expected


# ---------------------------------------------------------------------------
# (a) same-length truncation MID-SCALAR: length passes, event-scan catches
# ---------------------------------------------------------------------------
class TestSameLengthMidScalarCorruption:
    """The event-scan is load-bearing beyond the byte-length check."""

    def test_the_valid_dump_ends_with_a_status_scalar(self):
        """The premise the corruption is crafted against."""
        # Arrange
        last_scalar = "status: pending"
        # Act
        dump = GOOD_DUMP
        # Assert
        assert last_scalar in dump

    def test_the_corruption_is_the_same_byte_length(self):
        """Same length is the whole point — a length check cannot see it."""
        # Arrange
        good_size = len(GOOD_DUMP.encode("utf-8"))
        # Act
        bad_size = len(BAD_SAME_LENGTH.encode("utf-8"))
        # Assert
        assert bad_size == good_size

    def test_the_corruption_really_differs_from_the_valid_dump(self):
        """...and it is nonetheless a DIFFERENT file, not a no-op replace."""
        # Arrange
        good = GOOD_DUMP
        # Act
        bad = BAD_SAME_LENGTH
        # Assert
        assert bad != good

    def test_a_length_only_check_would_accept_the_corruption(self, tmp_path):
        """Prove the length check alone is satisfied by the corrupt bytes."""
        # Arrange
        tmp = _tmp_holding(tmp_path, BAD_SAME_LENGTH)
        # Act
        on_disk_size = os.stat(tmp).st_size
        # Assert
        assert on_disk_size == len(BAD_SAME_LENGTH.encode("utf-8"))

    def test_mid_scalar_corruption_same_length_is_caught(self, tmp_path):
        """Yet the verify still RAISES — the event-scan catches what the
        length check provably cannot."""
        # Arrange
        tmp = _tmp_holding(tmp_path, BAD_SAME_LENGTH)
        # Act
        # Assert
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, BAD_SAME_LENGTH)

    def test_the_corruption_really_is_unparseable(self):
        """Independent confirmation that the crafted bytes are genuinely
        malformed YAML (so the test above is not passing for a spurious
        reason) — a plain safe_load also rejects them."""
        # Arrange
        bad = BAD_SAME_LENGTH
        # Act
        # Assert
        with pytest.raises(yaml.YAMLError):
            yaml.load(bad, Loader=_SAFE_LOADER)


# ---------------------------------------------------------------------------
# (b) truncated mid-document (the 2026-06-13 incident shape) -> RAISES
# ---------------------------------------------------------------------------
class TestTruncatedMidDocument:
    """Prove the written bytes are FULLY reparseable, not merely non-empty."""

    def test_truncated_mid_scalar_raises(self, tmp_path):
        """Classic incident shape: the file ends in the MIDDLE of a quoted
        scalar (the canonical file "ended mid-string at line ~2784"). The
        opening quote is never closed -> the parser cannot reach StreamEnd."""
        # Arrange
        truncated = "tasks:\n- id: a\n  title: 'unterminated scalar that never clo"
        tmp = _tmp_holding(tmp_path, truncated)
        # Act
        # Assert
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, truncated)

    def test_truncation_of_a_real_dump_raises(self, tmp_path):
        """Take a REAL valid dump whose last field is a quoted scalar, then
        cut it off inside that scalar. Length matches what we pass as
        `dumped` (so the length check passes) — the event-scan rejects it."""
        # Arrange
        doc = {
            "tasks": [
                {
                    "id": "a",
                    "title": "A",
                    # A value with ': ' forces pyyaml to quote it, so a
                    # truncation inside it leaves an unterminated quoted scalar.
                    "note": "colon: forces quoting so truncation breaks parse",
                    "status": "pending",
                }
            ]
        }
        good = safe_dump(doc)
        cut = good[: good.index("forces")]  # ends mid-quoted-scalar
        tmp = _tmp_holding(tmp_path, cut)
        # Act
        # Assert
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, cut)


# ---------------------------------------------------------------------------
# (c) event-scan must REACH stream-end, not stop at the first event
# ---------------------------------------------------------------------------
#: A stream whose first several events parse fine (StreamStart, DocStart,
#: mapping start, the 'tasks' key, sequence start, the first task's scalars)
#: and which THEN ends mid-way through the second task's quoted scalar. A
#: first-event-only check would wrongly accept it; only a scan that runs to
#: StreamEnd rejects it. The three tests below split that one argument:
#: the early events DO parse, StreamEnd is NEVER reached, and the verify
#: rejects the file.
PARTIAL_STREAM = (
    "tasks:\n"
    "- id: a\n"
    "  title: A\n"
    "  status: pending\n"
    "- id: b\n"
    "  title: 'unterminated for b"  # EOF mid-quoted-scalar
)


class TestReachesStreamEnd:
    def test_large_valid_doc_accepted_after_full_consumption(self, tmp_path):
        """A large multi-item doc is accepted (no raise) ONLY because the scan
        consumes every event through StreamEndEvent. If the loop stopped at
        the first event this doc could not be distinguished from one that
        truncates after item 1 (see the tests below)."""
        # Arrange
        dumped = safe_dump(_valid_doc(2000))
        tmp = _tmp_holding(tmp_path, dumped)
        # Act
        out = _verify_dumped_tmp(tmp, dumped)
        # Assert
        assert out is None

    def test_the_partial_stream_parses_several_events_first(self, tmp_path):
        """Proves this is a "parses a few events then hits EOF" case, not an
        immediate syntax error at event 0."""
        # Arrange
        tmp = _tmp_holding(tmp_path, PARTIAL_STREAM)
        # Act
        events = _events_before_failure(tmp)
        # Assert — got past the stream/doc/mapping start events.
        assert len(events) >= 4

    def test_the_partial_stream_never_reaches_stream_end(self, tmp_path):
        # Arrange
        tmp = _tmp_holding(tmp_path, PARTIAL_STREAM)
        # Act
        events = _events_before_failure(tmp)
        # Assert
        assert not any(isinstance(e, yaml.events.StreamEndEvent) for e in events)

    def test_first_events_ok_then_eof_mid_stream_raises(self, tmp_path):
        """...and so the to-StreamEnd scan rejects the file."""
        # Arrange
        tmp = _tmp_holding(tmp_path, PARTIAL_STREAM)
        # Act
        # Assert
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, PARTIAL_STREAM)


# ---------------------------------------------------------------------------
# Byte-length short-write guard
# ---------------------------------------------------------------------------
class TestByteLengthGuard:
    """The on-disk file is SHORTER than the string we intended to write (a
    short / partial / disk-full write). The length check alone rejects it,
    before the event-scan even runs."""

    def test_short_write_raises_on_length_mismatch(self, tmp_path):
        # Arrange
        dumped = safe_dump(_valid_doc(3))
        tmp = _tmp_holding(tmp_path, dumped[: len(dumped) // 2])
        # Act
        # Assert
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, dumped)

    def test_short_write_error_names_the_size_mismatch(self, tmp_path):
        """The message must say WHICH check refused, or a 3am reader cannot
        tell a short write from a corrupt one."""
        # Arrange
        dumped = safe_dump(_valid_doc(3))
        tmp = _tmp_holding(tmp_path, dumped[: len(dumped) // 2])
        # Act
        message = _verify_error_message(tmp, dumped)
        # Assert
        assert "size" in message


# ---------------------------------------------------------------------------
# Perf note (informational) — evidences that the event-scan verify is cheaper
# than the full ``safe_load`` construct-reparse it replaces.
# ---------------------------------------------------------------------------
class TestPerfShape:
    def test_event_scan_verify_is_faster_than_full_safe_load(self, tmp_path):
        """The whole point of Fix B2: prove parseability WITHOUT constructing
        the document's Python object graph. On a synthetic realistic-shape
        store the event-scan measured ~2.4x faster than the old full
        ``safe_load`` construct-reparse (see PR body for numbers). We assert
        the RELATIVE property — event-scan strictly faster than the old
        approach — rather than an absolute wall-clock ceiling, so the test
        evidences the improvement without being flaky on loaded CI."""
        import io

        # Arrange — realistic card shape (notes + comments) so the construct
        # cost the old path paid is represented, not a degenerate doc.
        doc = {
            "tasks": [
                {
                    "id": f"t{i}",
                    "title": f"Title {i}",
                    "status": "pending",
                    "note": "lorem ipsum dolor sit amet " * 12,
                    "comments": [
                        {
                            "text": "a comment here " * 4,
                            "author": "x",
                            "ts": "2026-01-01",
                        }
                        for _ in range(3)
                    ],
                }
                for i in range(700)
            ],
            "users": {"alice": {"role": "dev"}},
        }
        dumped = safe_dump(doc)
        tmp = _tmp_holding(tmp_path, dumped)
        # Act
        # OLD approach: full safe_load construct-reparse (what we replaced).
        t0 = time.perf_counter()
        yaml.load(io.StringIO(dumped), Loader=_SAFE_LOADER)
        t_full_load = time.perf_counter() - t0

        # NEW approach: the event-scan verify.
        t0 = time.perf_counter()
        _verify_dumped_tmp(tmp, dumped)
        t_event_scan = time.perf_counter() - t0
        # Assert — strictly cheaper than the full construct it supersedes
        # (measured ~2.4x; assert only `<` to stay non-flaky on loaded CI).
        assert t_event_scan < t_full_load
