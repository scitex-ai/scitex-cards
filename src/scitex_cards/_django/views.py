#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Views for the scitex-todo board Django app.

``board_page`` renders the React SPA inside the scitex-ui workspace shell
(falling back to a server-rendered static graph when the built frontend assets
are absent). ``api_dispatch`` routes ``/<endpoint>`` to the ``HANDLERS`` dict.
"""

import logging
from pathlib import Path

from django.http import FileResponse, HttpResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .handlers import HANDLERS, NO_BOARD_ENDPOINTS
from .services import get_board

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "scitex_cards"
_FAVICON_PATH = _STATIC_DIR / "favicon.svg"


def _tasks_path_from_request(request):
    """Optional explicit store path from the ``?store=`` query param."""
    return request.GET.get("store") or None


def _api_base(request) -> str:
    """Mount-relative URL of the board root — the base every API fetch joins.

    The board frontend used to fetch site-root absolute URLs ("/graph",
    "/update", ...). Standalone (app mounted at "/") that worked; mounted by a
    host project under a prefix (e.g. ``path("apps/cards/", include(...))``)
    every fetch went to the HOST's site root and 404'd (hub card
    hub-cards-board-data-404). The base is DERIVED from wherever the app is
    actually mounted — never hardcoded — by reversing the board-root route:

    * mounted via ``include()`` → the URL name lives under the include's
      instance namespace (``request.resolver_match.namespace``, defaulting to
      the ``app_name`` ``"scitex_cards"``) → e.g. ``"/apps/cards/"``;
    * standalone (this urlconf IS ``ROOT_URLCONF``) → ``app_name`` creates no
      namespace, so the bare name resolves → ``"/"``.
    """
    from django.urls import NoReverseMatch, reverse

    match = getattr(request, "resolver_match", None)
    candidates = []
    if match is not None and getattr(match, "namespace", ""):
        candidates.append(f"{match.namespace}:board")
    candidates += ["scitex_cards:board", "board"]
    for name in candidates:
        try:
            base = reverse(name)
        except NoReverseMatch:
            continue
        return base if base.endswith("/") else base + "/"
    # Unreachable through any Django registration shape we know; fail loud
    # rather than emit fetches against the wrong root (no silent fallback).
    raise NoReverseMatch(
        "scitex-cards board root is not reversible "
        f"(tried url names: {', '.join(candidates)})"
    )


def favicon_view(request):
    """Serve the bundled SciTeX "S" SVG for the implicit `/favicon.ico` request.

    Modern browsers honor `Content-Type: image/svg+xml` for `.ico` URLs, so we
    serve the SVG directly. The standalone template also declares a
    `<link rel="icon" type="image/svg+xml">`, but browsers still request
    `/favicon.ico` on first visit before parsing <head>; without this route
    that request would fall through to `api_dispatch` and 404 (operator 3683).
    """
    if not _FAVICON_PATH.exists():
        return HttpResponseNotFound()
    # FileResponse handles streaming and the Content-Length header for us.
    return FileResponse(_FAVICON_PATH.open("rb"), content_type="image/svg+xml")


def board_page(request):
    """Serve the React SPA inside the scitex-ui shell, or a static fallback."""
    from django.template.loader import render_to_string

    built = (_STATIC_DIR / "assets" / "index.js").exists()

    if built:
        try:
            html = render_to_string(
                "scitex_cards/standalone.html",
                # DISPLAY string only (operator TG 2026-07-13). ``app_name``
                # stays ``scitex-todo`` — it keys the shell's static/asset
                # namespace, not the product name the operator reads.
                {"app_name": "scitex-todo", "app_label": "SciTeX Cards"},
                request=request,
            )
            return HttpResponse(html)
        except Exception:
            logger.exception("[scitex-todo] shell render failed; using fallback")

    # Fallback: server-rendered static graph (no Node/Vite build available).
    return HttpResponse(_static_graph_page(request))


def board_v3_page(request):
    """Serve the live board-v3 layout — operator's visual deliverable.

    Parallel to ``board_page`` (per lead a2a `62094366` — isolable, screen-
    shottable, A/B-comparable against the static :8052 prototype). Renders
    a self-contained HTML page that fetches ``/graph`` for real tasks.yaml
    data + renders the operator-co-designed layout (project columns +
    BLOCKING YOU panel + Resolve→``/resolve`` button per ADR-0006/0007).

    Server-rendered + inline-everything so it works regardless of Vite
    build state. The future React-SPA equivalent can re-render the same
    shape at the same URL when the FE rewrite lands.
    """
    from django.template.loader import render_to_string

    # Operator UX (TG 407): show the actual scitex-todo package version
    # in the page title AND the in-page header so the operator can verify
    # at a glance which release the board is running. Read __version__
    # straight off the package import — no second source of truth to drift.
    try:
        from scitex_cards import __version__ as _version
    except Exception:  # noqa: BLE001
        _version = "?"
    # PRODUCT NAME (operator TG 2026-07-13: "製品なので、scitex-todo ではなく、
    # SciTeX Cards としてタイトルを書いてください"). This is the DISPLAY string only
    # — the browser tab + the in-page header. The package, module, CLI, MCP
    # tool prefix and store path are all still `scitex-todo`; renaming those
    # is a separate, coordinated change.
    label = f"SciTeX Cards v{_version}"

    # SSOT status colors (kill the 4-bucket color collapse). The board's
    # color layer is single-sourced from ``STATUS_STYLE`` via the same
    # projection the /graph payload uses (``handlers.graph._status_colors``),
    # so the FIRST-PAINT CSS vars + the JS-driven mermaid/timeline color
    # exactly match the python-rendered mermaid artifacts. Do NOT re-derive
    # colors anywhere else — reuse this one map.
    from .handlers.graph import _status_colors

    status_colors = _status_colors()

    # PR (g) (lead a2a `ffc6629c80e4462a8401fb7e4ebb7240`, 2026-06-12):
    # one-shot boot announce of agents that have no turn URL configured,
    # so the operator sees the gap before any nudge / comment-relay
    # silently returns ok=false. Behind a module-level flag so we only
    # WARN once per process even if board_v3_page is hit many times.
    _maybe_announce_missing_turn_urls(request)

    try:
        html = render_to_string(
            "scitex_cards/board_v3.html",
            {
                "app_name": "scitex-todo",
                "app_label": label,
                "scitex_cards_version": _version,
                # Mount-relative API base ("/" standalone, "/<prefix>/" when a
                # host include()s the app). The template exposes it as the JS
                # const every fetch joins onto — see _api_base.
                "api_base": _api_base(request),
                # Per-status SSOT colors for first-paint CSS vars (board_v3
                # <head> renders a `:root{--status-fill-<s>...}` block from
                # this so cards/timeline/mermaid never collapse 7→4 colors).
                "status_colors": status_colors,
            },
            request=request,
        )
        return HttpResponse(html)
    except Exception:
        logger.exception("[scitex-todo] board_v3 render failed; using fallback")
        return HttpResponse(_static_graph_page(request))


def chat_page(request):
    """Serve the operator↔agent direct-message CHAT view (mobile-first).

    Minimal slice of the DM board pane (card
    ``fleet-agent-direct-message-board-pane-20260707``): agent list +
    per-agent thread + compose + history. Server-rendered template
    (``chat.html``, separate from the oversized ``board_v3.html``) whose JS
    lives in ``static/scitex_cards/chat/`` — ``chat_diff.js`` (pure render
    planning) then ``chat.js`` (DOM + network) — and polls the ``/dm/*``
    JSON endpoints (:mod:`.handlers.dm`) every ~5s, repainting the thread
    incrementally.
    """
    from django.template.loader import render_to_string

    try:
        from scitex_cards import __version__ as _version
    except Exception:  # noqa: BLE001
        _version = "?"
    html = render_to_string(
        "scitex_cards/chat.html",
        {
            "scitex_cards_version": _version,
            # Same mount-relative base as board_v3 — chat.js joins its
            # /dm/* polls onto it instead of the site root.
            "api_base": _api_base(request),
        },
        request=request,
    )
    return HttpResponse(html)


_TURN_URL_ANNOUNCED = False


def _maybe_announce_missing_turn_urls(request) -> None:
    """Boot-time WARN listing agents without a configured turn URL.

    Fires once per process (the module-level guard). The agent set is
    read from the live tasks.yaml via :func:`get_board` so the warning
    reflects whatever store the request resolves to.
    """
    global _TURN_URL_ANNOUNCED
    if _TURN_URL_ANNOUNCED:
        return
    _TURN_URL_ANNOUNCED = True
    try:
        from scitex_cards._push import announce_missing_at_boot

        board = get_board(_tasks_path_from_request(request))
        announce_missing_at_boot(list(board.tasks))
    except Exception:  # noqa: BLE001
        logger.exception(
            "[scitex-todo] turn-url boot announce failed (non-fatal)"
        )


def _static_graph_page(request) -> str:
    """Render a self-contained mermaid graph page (no React build needed).

    Uses mermaid.js from a CDN to draw the same ``build_mermaid`` source the
    PNG export uses, so the operator can view the graph even when the frontend
    toolchain has not produced a Vite bundle.
    """
    from scitex_cards._diagram import build_mermaid

    try:
        board = get_board(_tasks_path_from_request(request))
        mermaid_src = build_mermaid(board.tasks)
        store = str(board.store_path)
        count = len(board.tasks)
    except Exception as exc:  # surface the load error in the page, not a 500
        mermaid_src = ""
        store = ""
        count = 0
        error = str(exc)
    else:
        error = ""

    body = (
        f'<pre class="mermaid">{mermaid_src}</pre>'
        if mermaid_src
        else f'<p class="err">Failed to load task store: {error}</p>'
    )
    meta = (
        f'<p class="meta">{count} tasks &middot; <code>{store}</code></p>'
        if store
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SciTeX Cards</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #1e1e2e;
    color: #e0e0e0; margin: 0; padding: 24px; }}
  h1 {{ color: #7c5cbf; font-size: 1.3rem; }}
  .meta {{ color: #a0a0b0; font-size: 0.85rem; }}
  .err {{ color: #ff6b6b; }}
  code {{ background: #313145; padding: 2px 6px; border-radius: 4px; }}
  .mermaid {{ background: #fafafa; border-radius: 8px; padding: 16px; }}
</style>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, theme: "default" }});
</script>
</head>
<body>
  <h1>SciTeX Todo &mdash; dependency graph</h1>
  {meta}
  {body}
</body>
</html>"""


def _get_board(request):
    """Return the board for this request, or None when the store can't load."""
    try:
        return get_board(_tasks_path_from_request(request))
    except FileNotFoundError:
        logger.warning("[scitex-todo] task store not found")
        return None


@csrf_exempt
def api_dispatch(request, endpoint):
    """Dispatch ``/<endpoint>`` to its handler function."""
    handler = HANDLERS.get(endpoint)
    if handler is None:
        return JsonResponse({"error": f"Unknown endpoint: {endpoint}"}, status=404)

    if endpoint in NO_BOARD_ENDPOINTS:
        return handler(request, None)

    board = _get_board(request)
    if board is None:
        return JsonResponse({"error": "No task store found."}, status=400)

    try:
        return handler(request, board)
    except Exception as exc:
        logger.exception("[scitex-todo] API error on /%s", endpoint)
        return JsonResponse({"error": str(exc)}, status=500)


# EOF
