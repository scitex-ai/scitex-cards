#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat-attachment STORAGE — the one layout, shared by both ways in.

Two callers put files here and ONE renderer reads them back:

* the operator's browser, via ``POST /dm/upload``
  (:mod:`scitex_cards._django.handlers.attachments`), and
* an AGENT, via the ``dm_send_document`` MCP tool, which had no entry point
  at all until this module existed. An agent asked which API to use to send
  a PDF and the honest answer was "none" — the PDF arrived as prose
  describing a PDF.

Both land in the SAME place with the SAME URL shape, so the chat pane's
existing renderer serves both without knowing which side produced the file.
A second storage layout would have meant a second renderer, and then two
half-working ones.

LAYOUT (do not fork it)
-----------------------
``<store_dir>/attachments/<YYYY-MM>/<uuid-hex>/<original-name>`` and the
reference a message carries is the RELATIVE url ``attachments/<YYYY-MM>/
<uuid-hex>/<name>``. The uuid directory is what makes keeping the original
name safe: two ``report.pdf`` uploads cannot collide, and the browser still
offers the right filename without a ``Content-Disposition`` header.

WHY A COPY, NEVER A REFERENCE
-----------------------------
:func:`store_local_file` takes a filesystem path and COPIES the bytes in.
It never records the caller's path, and nothing downstream ever serves a
file from where the caller found it. That is deliberate: the serve path is
reachable over the operator's tunnel, so "serve this path" would be an
arbitrary-file-read endpoint wearing a chat feature's clothes. The copy is
the containment.

For the same reason this module is NOT a backend verb: ``_server.py``
dispatches every name in ``BACKEND_VERBS`` over HTTP, so a path-taking verb
in that tuple would be remotely callable. ``dm_send_document`` composes
this module with ``dm_send`` inside the agent's own process instead.
"""

from __future__ import annotations

import mimetypes
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

#: Hard ceiling per file. Generous enough for screenshots, logs and PDFs;
#: small enough that a single request cannot fill the disk. A disk-full board
#: is a fleet outage, so this is a guard, not a preference.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: First segment of every attachment url. Kept as a constant because the chat
#: pane recognises an attachment line by exactly this prefix.
URL_PREFIX = "attachments"

#: A stored directory component must be exactly this shape. Anything else is
#: refused before it is joined to a path, so ``..`` never gets the chance.
_UUID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_SUBDIR_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}\Z")

#: Copy granularity. Small enough that an over-size file is caught long
#: before it is fully written.
_CHUNK_BYTES = 1024 * 1024


class AttachmentError(ValueError):
    """A caller-fixable refusal: missing file, wrong kind, over the limit."""


def attachments_root(store=None) -> Path:
    """``<store_dir>/attachments`` — beside the store, never inside it.

    Resolved from the STORE, not from the DM sidecar. This used to read
    ``threads_path(store).parent``, which located the attachments directory via
    a file that is being retired (``docs/design/dm-into-cards-db-migration.md``
    §7.2) — and, worse, via a path query that used to CREATE that file as a
    side effect. Same directory either way, so no attachment moves; the change
    is that the coupling is gone. (Carried over from #592's fix when the layout
    moved here; the decoupling must not be lost in the move.)
    """
    from scitex_cards._paths import resolve_tasks_path

    return Path(resolve_tasks_path(store)).parent / URL_PREFIX


def safe_name(raw: str) -> str:
    """A filename reduced to its basename, with separators stripped.

    Keeps the extension (it drives inline rendering) and keeps the name
    human-readable, but cannot escape its directory.
    """
    name = Path(raw or "").name.strip() or "upload"
    name = name.replace("\\", "_").replace("/", "_")
    return name[:120]


def url_for(subdir: str, token: str, name: str) -> str:
    """The relative url a message body carries for a stored file."""
    return f"{URL_PREFIX}/{subdir}/{token}/{name}"


def _new_slot(store) -> tuple[str, str, Path]:
    """Mint an unused ``(subdir, token, directory)`` under the root."""
    subdir = datetime.now(timezone.utc).strftime("%Y-%m")
    token = uuid.uuid4().hex
    target_dir = attachments_root(store) / subdir / token
    target_dir.mkdir(parents=True, exist_ok=True)
    return subdir, token, target_dir


def _describe(target: Path, subdir: str, token: str, name: str, mime: str) -> dict:
    """The metadata block both entry points return, one shape."""
    return {
        "url": url_for(subdir, token, name),
        "filename": name,
        "mime_type": mime,
        "size": target.stat().st_size,
    }


def store_chunks(chunks, filename: str, *, mime: str | None = None, store=None) -> dict:
    """Write an iterable of byte chunks into a fresh slot.

    The size ceiling is enforced AS THE BYTES ARRIVE, not from a
    caller-declared length: a declared size is a claim, and this is the layer
    that has to be right about it. An over-size stream is aborted and its
    partial directory removed, so a refusal leaves nothing behind.
    """
    name = safe_name(filename)
    subdir, token, target_dir = _new_slot(store)
    target = target_dir / name
    written = 0
    try:
        with target.open("wb") as handle:
            for chunk in chunks:
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise AttachmentError(
                        f"file exceeds the {MAX_UPLOAD_BYTES}-byte limit"
                    )
                handle.write(chunk)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    guessed = mimetypes.guess_type(name)[0]
    return _describe(
        target, subdir, token, name, mime or guessed or "application/octet-stream"
    )


def store_local_file(
    source: str | Path, *, filename: str | None = None, store=None
) -> dict:
    """COPY a file the caller can already read into the attachment store.

    The agent-side entry point. ``source`` is read once and copied; the path
    is never recorded and never served from. Returns the same metadata block
    as an operator-side upload, so one renderer serves both.

    Raises :class:`AttachmentError` for a path that is missing, is not a
    regular file, or is over :data:`MAX_UPLOAD_BYTES` — all of which are the
    caller's to fix, and none of which should look like a server fault.
    """
    path = Path(source).expanduser()
    if not path.exists():
        raise AttachmentError(f"no such file: {path}")
    if not path.is_file():
        raise AttachmentError(f"not a regular file: {path}")
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise AttachmentError(
            f"file is {size} bytes, over the {MAX_UPLOAD_BYTES}-byte limit"
        )

    def _read():
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk

    return store_chunks(_read(), filename or path.name, store=store)


def resolve_stored(subdir: str, token: str, name: str, store=None) -> Path | None:
    """The containment check, in ONE place: components in, real file or None.

    Every component is matched against its exact shape before it is joined,
    and the RESOLVED path is then re-checked to still be inside the root —
    belt and braces, because this is the half that is reachable over a
    tunnel. Anything that fails either test is indistinguishable from a
    missing file to the caller.
    """
    if not _SUBDIR_RE.match(subdir or "") or not _UUID_RE.match(token or ""):
        return None
    root = attachments_root(store).resolve()
    path = (root / subdir / token / safe_name(name)).resolve()
    if not str(path).startswith(str(root) + "/") or not path.is_file():
        return None
    return path


def guess_mime(name: str) -> str:
    """Content type for a stored name, defaulting to opaque bytes."""
    return mimetypes.guess_type(safe_name(name))[0] or "application/octet-stream"


__all__ = [
    "MAX_UPLOAD_BYTES",
    "URL_PREFIX",
    "AttachmentError",
    "attachments_root",
    "guess_mime",
    "resolve_stored",
    "safe_name",
    "store_chunks",
    "store_local_file",
    "url_for",
]

# EOF
