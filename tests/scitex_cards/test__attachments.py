#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The attachment store: ONE layout, a copy in, a containment check out.

Agents had no way to attach a file to a DM — ``dm_send`` took ``to`` and
``body`` and nothing else — so a PDF reached the operator as prose describing
a PDF. The fix adds a second way IN, which is exactly the moment a codebase
grows a second storage layout and then a second half-working renderer. These
tests pin the properties that stop that:

* an agent-side copy lands under the SAME ``attachments/<YYYY-MM>/<uuid>/``
  layout and produces the SAME url shape as an operator-side upload;
* the ORIGINAL file is never depended on — the bytes are copied, so deleting
  the source afterwards leaves the attachment intact;
* the size ceiling and the containment check still hold.

Every test drives an EXPLICIT tmp store. Resolving the default here would
point the suite at the live fleet board.
"""

from __future__ import annotations

import pytest

from scitex_cards import _attachments
from scitex_cards._attachments import (
    MAX_UPLOAD_BYTES,
    AttachmentError,
    attachments_root,
    resolve_stored,
    store_local_file,
    url_for,
)


@pytest.fixture
def store(tmp_path):
    """An explicit tmp task-store path — never the resolved default."""
    return str(tmp_path / "cards.db")


@pytest.fixture
def source_pdf(tmp_path):
    """A file on disk the "agent" wants to send."""
    path = tmp_path / "loan-contract.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf, but bytes are bytes\n")
    return path


@pytest.fixture
def stored(source_pdf, store):
    """The metadata returned by copying that file into the store."""
    return store_local_file(source_pdf, store=store)


def test_a_stored_file_keeps_its_original_name(stored):
    # Arrange — done by the `stored` fixture.
    # Act
    filename = stored["filename"]
    # Assert
    assert filename == "loan-contract.pdf"


def test_the_url_uses_the_shared_attachments_prefix(stored):
    """The chat pane recognises an attachment by exactly this prefix."""
    # Arrange
    url = stored["url"]
    # Act
    prefix = url.split("/")[0]
    # Assert
    assert prefix == "attachments"


def test_the_url_has_the_month_uuid_name_shape(stored):
    """Same four segments an operator-side upload produces — one renderer."""
    # Arrange
    url = stored["url"]
    # Act
    segments = url.split("/")
    # Assert
    assert len(segments) == 4


def test_the_uuid_directory_is_a_bare_hex_token(stored):
    # Arrange
    url = stored["url"]
    # Act
    token = url.split("/")[2]
    # Assert
    assert len(token) == 32


def test_the_bytes_land_under_the_attachments_root(stored, store):
    # Arrange
    root = attachments_root(store)
    # Act
    landed = (root / "/".join(stored["url"].split("/")[1:])).is_file()
    # Assert
    assert landed


def test_the_stored_size_matches_the_source(stored, source_pdf):
    # Arrange
    expected = source_pdf.stat().st_size
    # Act
    size = stored["size"]
    # Assert
    assert size == expected


def test_a_pdf_is_stored_with_its_real_content_type(stored):
    """The mime type drives how the chat pane decides to render it."""
    # Arrange — done by the `stored` fixture.
    # Act
    mime = stored["mime_type"]
    # Assert
    assert mime == "application/pdf"


def test_deleting_the_source_does_not_break_the_attachment(stored, source_pdf, store):
    """A COPY, not a reference — this is the security property, not a nicety.

    Serving from the caller's path would make the serve endpoint an
    arbitrary-file read, and it would also break the moment an agent tidied up
    its scratch directory.
    """
    # Arrange
    source_pdf.unlink()
    # Act
    subdir, token, name = stored["url"].split("/")[1:]
    # Assert
    assert resolve_stored(subdir, token, name, store=store) is not None


def test_the_stored_copy_is_not_the_source_path(stored, source_pdf, store):
    # Arrange
    subdir, token, name = stored["url"].split("/")[1:]
    # Act
    served = resolve_stored(subdir, token, name, store=store)
    # Assert
    assert served != source_pdf


def test_a_missing_source_is_refused(store, tmp_path):
    # Arrange
    absent = tmp_path / "not-here.pdf"

    # Act
    def send():
        store_local_file(absent, store=store)

    # Assert
    with pytest.raises(AttachmentError):
        send()


def test_a_directory_is_not_a_sendable_file(store, tmp_path):
    # Arrange
    folder = tmp_path / "a-folder"
    folder.mkdir()

    # Act
    def send():
        store_local_file(folder, store=store)

    # Assert
    with pytest.raises(AttachmentError):
        send()


def test_the_shipped_ceiling_is_25_mib():
    """The VALUE, pinned separately from the ENFORCEMENT.

    Written this way after a mutation probe embarrassed the first version:
    that test sized its input as ``MAX_UPLOAD_BYTES + 1``, read from the
    module under test, so raising the constant raised the test's own input
    with it and the guard stayed green (while cheerfully writing 25 GB to
    disk). A test must not take its expectation from the thing it is
    measuring.
    """
    # Arrange
    expected = 25 * 1024 * 1024
    # Act
    shipped = MAX_UPLOAD_BYTES
    # Assert
    assert shipped == expected


@pytest.fixture
def tiny_ceiling():
    """A small ceiling PASSED IN, so enforcement is testable without a 25MB file.

    Both store verbs take `max_bytes`, so the limit under test is an argument
    rather than a rebound module constant. That matters beyond the no-mocks
    rule: patching `_attachments.MAX_UPLOAD_BYTES` asserts against the test's
    own edit, so the test stays green if a verb later stops consulting the
    constant. Passing it exercises the parameter production actually reads.
    """
    return 64


def test_a_file_over_the_ceiling_is_refused(store, tmp_path, tiny_ceiling):
    """The ceiling guards against a disk-full board, which is a fleet outage."""
    # Arrange
    fat = tmp_path / "over.bin"
    fat.write_bytes(b"\0" * (tiny_ceiling + 1))

    # Act
    def send():
        store_local_file(fat, store=store, max_bytes=tiny_ceiling)

    # Assert
    with pytest.raises(AttachmentError):
        send()


def test_a_file_at_the_ceiling_is_accepted(store, tmp_path, tiny_ceiling):
    """The boundary is a ceiling, not an off-by-one that rejects a legal file."""
    # Arrange
    exact = tmp_path / "exact.bin"
    exact.write_bytes(b"\0" * tiny_ceiling)
    # Act
    meta = store_local_file(exact, store=store, max_bytes=tiny_ceiling)
    # Assert
    assert meta["size"] == tiny_ceiling


def test_a_stream_that_overruns_mid_write_is_refused(store, tiny_ceiling):
    """A DECLARED size is a claim; the bytes are what count.

    ``store_chunks`` is what the browser upload path feeds, and there the
    length is whatever the client says it is. This drives the enforcement
    that happens as the bytes arrive.
    """
    # Arrange
    chunks = [b"\0" * 32] * 4

    # Act
    def send():
        _attachments.store_chunks(
            iter(chunks), "over.bin", store=store, max_bytes=tiny_ceiling
        )

    # Assert
    with pytest.raises(AttachmentError):
        send()


@pytest.fixture
def after_an_over_size_refusal(store, tiny_ceiling):
    """Drive a refusal, then hand back the store root for inspection.

    The refusal lives in the FIXTURE rather than the test body: a
    ``pytest.raises`` block counts as an assertion, so keeping it inline
    alongside the leftovers check would make this two assertions in one test.
    """
    with pytest.raises(AttachmentError):
        _attachments.store_chunks(
            iter([b"\0" * 32] * 4), "over.bin", store=store, max_bytes=tiny_ceiling
        )
    return attachments_root(store)


def test_an_over_size_refusal_leaves_no_partial_directory(after_an_over_size_refusal):
    """A refusal must not litter the store with half a file."""
    # Arrange
    root = after_an_over_size_refusal

    # Act
    leftovers = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []

    # Assert
    assert leftovers == []


def test_a_symlink_out_of_the_store_is_not_served(store, stored, tmp_path):
    """THE containment check, exercised for real.

    The first version of this test passed ``../../etc/passwd`` as the name and
    was green even with the containment check deleted — because ``safe_name``
    had already reduced it to ``passwd``, which does not exist. It was
    measuring basename reduction and calling it containment.

    A symlink is the case that actually reaches the check: every path
    COMPONENT is well-formed, so the shape guards admit it, and only the
    re-check on the RESOLVED path notices that it lands outside the root.
    """
    # Arrange — plant a well-named symlink pointing out of the store.
    _, subdir, token, _ = stored["url"].split("/")
    outside = tmp_path / "secret.txt"
    outside.write_text("not yours")
    link = attachments_root(store) / subdir / token / "innocent.txt"
    link.symlink_to(outside)
    # Act
    resolved = resolve_stored(subdir, token, "innocent.txt", store=store)
    # Assert
    assert resolved is None


def test_a_traversal_name_is_reduced_to_its_basename(store, stored):
    """The FIRST line of defence, named for what it actually does."""
    # Arrange
    hostile = "../../../../etc/passwd"
    # Act
    reduced = _attachments.safe_name(hostile)
    # Assert
    assert reduced == "passwd"


def test_a_traversal_token_never_resolves(store, stored):
    """``..`` is refused on shape before it is ever joined to a path."""
    # Arrange
    subdir = stored["url"].split("/")[1]
    # Act
    resolved = resolve_stored(subdir, "..", "cards.db", store=store)
    # Assert
    assert resolved is None


def test_a_traversal_name_never_resolves_to_a_file(store, stored):
    """End-to-end on the hostile name: reduced, then simply not found.

    Kept alongside the symlink test above, which is the one that actually
    exercises the resolved-path containment re-check — this one would stay
    green without it.
    """
    # Arrange
    _, subdir, token, _ = stored["url"].split("/")
    # Act
    resolved = resolve_stored(subdir, token, "../../../../etc/passwd", store=store)
    # Assert
    assert resolved is None


def test_a_malformed_subdir_never_resolves(store, stored):
    # Arrange
    token = stored["url"].split("/")[2]
    # Act
    resolved = resolve_stored("not-a-month", token, "loan-contract.pdf", store=store)
    # Assert
    assert resolved is None


def test_url_for_matches_what_the_store_produced(stored):
    """The url builder and the storage path are ONE fact, not two."""
    # Arrange
    _, subdir, token, name = stored["url"].split("/")
    # Act
    rebuilt = url_for(subdir, token, name)
    # Assert
    assert rebuilt == stored["url"]


def test_two_files_with_the_same_name_do_not_collide(source_pdf, store):
    """The uuid directory is what makes keeping the original name safe."""
    # Arrange
    first = store_local_file(source_pdf, store=store)
    # Act
    second = store_local_file(source_pdf, store=store)
    # Assert
    assert first["url"] != second["url"]


# EOF
