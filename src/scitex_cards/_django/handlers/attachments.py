#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chat attachments — the HTTP half (upload from the browser, serve back).

The operator's blocker, stated plainly: "今まだテキストしかやりとりできていない"
(still text-only). This handler is the operator's way in — a file picked,
pasted or dropped in the composer.

It is no longer the ONLY way in, which is why the layout moved out. Agents
send files too (``dm_send_document``), and if the two sides had grown their
own storage they would have grown two renderers with it. Everything about
WHERE a file lives, what a stored url looks like, the size ceiling and the
containment check now lives in :mod:`scitex_cards._attachments`; this module
is the Django adapter over it and holds no layout knowledge of its own.

DESIGN NOTES worth keeping:

* The served path is looked up by ``(subdir, uuid, name)`` and re-validated to
  live INSIDE the attachments root. A caller-supplied path never reaches the
  filesystem un-checked — the same class of bug as the ``?store=`` seam found
  in ``dm.py``, and this handler starts closed.

* Size is bounded on the SERVER as the bytes arrive, not trusted from the
  client, because the client is the thing being defended against.

* There is deliberately NO endpoint that takes a filesystem path. The agent
  entry point copies bytes into the store instead of naming a file to serve;
  a path parameter here would be an arbitrary-file-read hole, since this
  surface is reachable over the operator's tunnel.
"""

from __future__ import annotations

from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from scitex_cards._attachments import (
    MAX_UPLOAD_BYTES,
    AttachmentError,
    guess_mime,
    resolve_stored,
    store_chunks,
)
from scitex_cards._attachments import attachments_root as _attachments_root


def attachments_root(store=None):
    """``<store_dir>/attachments`` — re-exported for the handler's callers."""
    return _attachments_root(store)


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
    # `store=None` on purpose: an upload resolves the store SERVER-SIDE and
    # never from the request, for the same reason dm.py's write path does.
    try:
        meta = store_chunks(
            upload.chunks(),
            upload.name,
            mime=upload.content_type or None,
            store=None,
        )
    except AttachmentError as exc:
        return JsonResponse({"error": str(exc), "limit": MAX_UPLOAD_BYTES}, status=413)
    return JsonResponse(meta)


def serve_view(
    request: HttpRequest, subdir: str, token: str, name: str
) -> HttpResponse:
    """Serve a stored attachment, by validated components only."""
    path = resolve_stored(subdir, token, name, store=None)
    if path is None:
        return JsonResponse({"error": "not-found"}, status=404)
    return FileResponse(path.open("rb"), content_type=guess_mime(name))


__all__ = ["upload_view", "serve_view", "attachments_root", "MAX_UPLOAD_BYTES"]

# EOF
