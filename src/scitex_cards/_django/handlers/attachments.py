#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat attachments — upload and serve.

The operator's blocker, stated plainly: "今まだテキストしかやりとりできていない"
(still text-only). This is the smallest end-to-end slice that fixes it — send a
file, see it in the thread — rather than the full media stack.

DESIGN NOTES worth keeping:

* Files land under ``<store_dir>/attachments/<YYYY-MM>/<uuid>/<original-name>``.
  The UUID directory is what makes the name safe to keep: two uploads called
  ``screenshot.png`` cannot collide, and the browser still offers the right
  filename on download without a ``Content-Disposition`` header.

* The served path is looked up by ``(subdir, uuid, name)`` and re-validated to
  live INSIDE the attachments root. A caller-supplied path never reaches the
  filesystem un-checked — the same class of bug as the ``?store=`` seam found
  in ``dm.py`` today, and this handler is new code so it starts closed.

* Size and type are bounded here rather than trusted from the client, because
  the client is the thing being defended against.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

#: Hard ceiling per file. Generous enough for screenshots, logs and PDFs;
#: small enough that a single request cannot fill the disk. A disk-full board
#: is a fleet outage, so this is a guard, not a preference.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: A stored directory component must be exactly this shape. Anything else is
#: refused before it is joined to a path, so ``..`` never gets the chance.
_UUID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_SUBDIR_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}\Z")


def attachments_root(store=None) -> Path:
    """``<store_dir>/attachments`` — beside the store, never inside it.

    Resolved from the STORE, not from the DM sidecar. This used to read
    ``threads_path(store).parent``, which located the attachments directory via
    a file that is being retired (``docs/design/dm-into-cards-db-migration.md``
    §7.2) — and, worse, via a path query that used to CREATE that file as a
    side effect. Same directory either way, so no attachment moves; the change
    is that the coupling is gone.
    """
    from scitex_cards._paths import resolve_tasks_path

    return Path(resolve_tasks_path(store)).parent / "attachments"


def _safe_name(raw: str) -> str:
    """A filename reduced to its basename, with separators stripped.

    Keeps the extension (it drives inline rendering) and keeps the name
    human-readable, but cannot escape its directory.
    """
    name = Path(raw or "").name.strip() or "upload"
    name = name.replace("\\", "_").replace("/", "_")
    return name[:120]


@csrf_exempt
def upload_view(request: HttpRequest) -> HttpResponse:
    """POST a file, get back the metadata a message carries."""
    if request.method != "POST":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse(
            {"error": "upload requires a 'file' part (multipart/form-data)"},
            status=400,
        )
    if upload.size > MAX_UPLOAD_BYTES:
        return JsonResponse(
            {
                "error": (
                    f"file is {upload.size} bytes, over the "
                    f"{MAX_UPLOAD_BYTES}-byte limit"
                ),
                "limit": MAX_UPLOAD_BYTES,
            },
            status=413,
        )

    name = _safe_name(upload.name)
    subdir = datetime.now(timezone.utc).strftime("%Y-%m")
    token = uuid.uuid4().hex
    # `store=None` on purpose: an upload resolves the store SERVER-SIDE and
    # never from the request, for the same reason dm.py's write path does.
    target_dir = attachments_root(None) / subdir / token
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    with target.open("wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)

    mime = (
        upload.content_type
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )
    return JsonResponse(
        {
            "url": f"attachments/{subdir}/{token}/{name}",
            "filename": name,
            "mime_type": mime,
            "size": target.stat().st_size,
        }
    )


def serve_view(
    request: HttpRequest, subdir: str, token: str, name: str
) -> HttpResponse:
    """Serve a stored attachment, by validated components only."""
    if not _SUBDIR_RE.match(subdir or "") or not _UUID_RE.match(token or ""):
        return JsonResponse({"error": "not-found"}, status=404)
    safe = _safe_name(name)
    root = attachments_root(None).resolve()
    path = (root / subdir / token / safe).resolve()
    # Belt and braces: even with the component checks above, confirm the
    # resolved path is still inside the root before opening it.
    if not str(path).startswith(str(root) + "/") or not path.is_file():
        return JsonResponse({"error": "not-found"}, status=404)
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return FileResponse(path.open("rb"), content_type=mime)


__all__ = ["upload_view", "serve_view", "attachments_root", "MAX_UPLOAD_BYTES"]

# EOF
