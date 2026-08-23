#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An attachment url replicates; the bytes behind it do not.

Measured 2026-08-23. An 84KB file was sent from compute-04 to an agent on
compute-03 with ``dm_send_document``. The DM record crossed, the bytes did
not, and BOTH SIDES REPORTED SUCCESS: the sender got a url and a byte count,
the recipient got a message whose last line was that url. No error anywhere.

The cause is not a transport bug — it is a shape that had nowhere to say
"readable from one host". ``attachments_root()`` is a local directory while
the record carrying the reference replicates to every seat, so the reference
outruns the thing it references.

These tests pin the two halves of the honest answer:

* the WRITE side names the host that holds the bytes, and declares that the
  storage layout does not replicate them (``_describe``); and
* the READ side can say "not here", distinguishing a dead link from no link
  (``attachment_status``).

They cannot reproduce the cross-host failure — a single-host test suite is
precisely the instrument that could not see this, which is why the first
report came from a peer on another machine and not from CI. What they DO
guard is that the returned shape carries the fact a caller needs in order to
notice.
"""

from __future__ import annotations

import pytest

from scitex_cards._attachments import (
    URL_PREFIX,
    attachment_status,
    store_local_file,
)


@pytest.fixture
def store(tmp_path):
    """A store label whose parent directory holds the attachments root."""
    return str(tmp_path / "cards.db")


@pytest.fixture
def source_pdf(tmp_path):
    """A real file on disk for the sender to hand over."""
    path = tmp_path / "survivors.json"
    path.write_bytes(b'{"rows": []}')
    return str(path)


@pytest.fixture
def stored(source_pdf, store):
    """The metadata block a send returns."""
    return store_local_file(source_pdf, store=store)


def test_the_stored_metadata_names_the_host_holding_the_bytes(stored):
    # Arrange — done by the `stored` fixture.
    # Act
    host = stored.get("host")
    # Assert
    assert host


def test_the_stored_metadata_declares_the_bytes_unreplicated(stored):
    # Arrange — done by the `stored` fixture.
    # Act
    replicated = stored["replicated"]
    # Assert
    assert replicated is False


def test_the_reader_finds_bytes_that_were_stored_on_this_host(stored, store):
    # Arrange — done by the `stored` fixture.
    # Act
    status = attachment_status(stored["url"], store=store)
    # Assert
    assert status["present"] is True


def test_the_reader_reports_absent_for_a_url_with_no_bytes_behind_it(store):
    # Arrange
    orphan = f"{URL_PREFIX}/2026-08/982f00c6380849c7aa5774f258033383/gone.json"
    # Act
    status = attachment_status(orphan, store=store)
    # Assert
    assert status["present"] is False


def test_the_absence_reason_names_the_host_that_answered(store):
    # Arrange
    orphan = f"{URL_PREFIX}/2026-08/982f00c6380849c7aa5774f258033383/gone.json"
    # Act
    status = attachment_status(orphan, store=store)
    # Assert
    assert status["host"] in status["reason"]


def test_a_string_that_is_not_an_attachment_url_says_so(store):
    # Arrange
    prose = "I have attached the file, see above"
    # Act
    status = attachment_status(prose, store=store)
    # Assert
    assert status["reason"] == "not an attachment url"


def test_every_answer_carries_the_same_four_keys(store):
    # Arrange
    prose = "not a url at all"
    # Act
    status = attachment_status(prose, store=store)
    # Assert
    assert set(status) == {"url", "present", "host", "reason"}


def test_a_present_file_still_reports_which_host_answered(stored, store):
    # Arrange — done by the `stored` fixture.
    # Act
    status = attachment_status(stored["url"], store=store)
    # Assert
    assert status["host"] == stored["host"]


def test_the_url_shape_is_unchanged_by_the_added_fields(stored):
    # Arrange — the over-reach control: the layout must not have moved.
    # Act
    url = stored["url"]
    # Assert
    assert url.startswith(f"{URL_PREFIX}/")


def test_the_size_is_still_reported(stored):
    # Arrange — the over-reach control: existing callers read this.
    # Act
    size = stored["size"]
    # Assert
    assert size == 12

# EOF
