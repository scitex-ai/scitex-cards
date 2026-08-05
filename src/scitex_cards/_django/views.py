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

from ._request_store import read_store
from .handlers import HANDLERS, NO_BOARD_ENDPOINTS
from .services import get_board

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "scitex_cards"
_FAVICON_PATH = _STATIC_DIR / "favicon.svg"


def _tasks_path_from_request(request):
    """The store this READ resolves to — see :mod:`._request_store`.

    A trusted middleware's ``request.scitex_store`` wins over the caller's
    ``?store=``; this used to read the query and nothing else, which is why
    scitex-hub had to overwrite the query rather than simply setting the
    attribute.
    """
    return read_store(request)


def _include_root(path: str, aliases: tuple[str, ...]) -> str:
    """Recover the app's include root from a page's own ``request.path``.

    A page served at ``<include-root><alias>`` must prefix every fetch and
    every in-app link with ``<include-root>`` — "/" standalone, "/apps/cards/"
    on the hub. The root ("") route needs no strip; every OTHER spelling of
    the same page sits one segment deeper and does.

    The alias is only stripped when it is a WHOLE trailing SEGMENT — i.e. the
    path IS the alias, or it ends with ``"/" + alias``. The naive
    ``endswith(alias)`` this replaces would have eaten the tail of an
    unrelated mount: with ``/board`` now an alias, a hub mounting this app at
    ``/apps/scoreboard/`` would have had its include root rewritten to
    ``/apps/score`` and every call on the operator's board would 404 — the
    exact class of bug #556 and #557 were.
    """
    for alias in aliases:
        for candidate in (alias + "/", alias):
            if path == candidate or path.endswith("/" + candidate):
                return path[: len(path) - len(candidate)]
    return path


#: Every route in ``urls.py`` that serves the BOARD page, longest first so
#: ``board-v3`` is tested before its ``board`` prefix-mate. The root ("")
#: route is absent on purpose: there is nothing to strip there.
_BOARD_ALIASES = ("board-v3", "board")

#: Every route that serves the DM page. ``chat`` is the ORIGINAL published
#: spelling and stays first; ``dm`` is the name the operator asked for.
_DM_ALIASES = ("chat", "dm")


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
    a self-contained HTML page that fetches ``/graph`` for real task-store
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

    # Mount-aware API base (P1, scitex-hub): the hub mounts this board under
    # a sub-path (e.g. /apps/cards/), where the template's former root-absolute
    # fetches 404'd — https://scitex.ai/graph → 404 while
    # https://scitex.ai/apps/cards/graph → 200. ``request.path`` is the board
    # page's own URL; for the "" (root) route that IS the include root. The
    # /board-v3 and /board aliases serve the same view one path segment
    # deeper, so strip that trailing segment to recover the include root there
    # too. See :func:`_include_root` for why the strip is segment-anchored.
    api_base = _include_root(request.path, _BOARD_ALIASES)

    try:
        html = render_to_string(
            "scitex_cards/board_v3.html",
            {
                "app_name": "scitex-todo",
                "app_label": label,
                "scitex_cards_version": _version,
                # Include-root prefix for every board fetch (see above). The
                # template strips trailing slashes, so a root mount renders
                # API_BASE == "" and calls stay "/graph"-shaped.
                "api_base": api_base,
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

    # Mount-aware API base — same contract as board_v3_page (see there for the
    # full story). The chat page is served at "<include-root>chat" and, since
    # 2026-07-29, also at "<include-root>dm", so stripping its own trailing
    # segment off request.path recovers the include root the /dm/* fetches must
    # be prefixed with ("/apps/cards/" on the hub, "/" standalone). Serving the
    # page at /dm without teaching this the new alias would have left
    # api_base == "/dm", pointing every DM poll at "/dm/dm/threads" and every
    # switcher link at "/dmchat" — the page would render and then do nothing.
    # chat.html ALWAYS sets window.API_BASE from this; chat.js refuses to run
    # without it (a missing marker is an integration bug, never a silent
    # root-mount guess).
    api_base = _include_root(request.path, _DM_ALIASES)

    html = render_to_string(
        "scitex_cards/chat.html",
        {"scitex_cards_version": _version, "api_base": api_base},
        request=request,
    )
    return HttpResponse(html)


_TURN_URL_ANNOUNCED = False


def _maybe_announce_missing_turn_urls(request) -> None:
    """Boot-time WARN listing agents without a configured turn URL.

    Fires once per process (the module-level guard). The agent set is
    read from the live store via :func:`get_board` so the warning
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
        logger.exception("[scitex-todo] turn-url boot announce failed (non-fatal)")


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


#: Endpoints allowed to answer from a board that is one refresh-cycle behind.
#: STRICTLY read-only VIEW payloads: they repaint on a poll, so a stale answer
#: is invisible, while a blocking rebuild costs the full store parse (measured
#: 4.6s, and 31s for the board end-to-end on the live store).
#:
#: Nothing that WRITES may be listed here, and nothing that must reflect a
#: write it just made — the chat POST reads its own message back through the
#: board, and adding it here would make the operator's posted comment vanish
#: until the next refresh. Read-your-own-writes beats latency; the default is
#: strict and membership here is the deliberate exception.
STALE_OK_ENDPOINTS = frozenset({"graph", "timeline"})


def _get_board(request, *, allow_stale: bool = False):
    """Return the board for this request. RAISES when the store can't be read.

    IT NO LONGER SWALLOWS ``FileNotFoundError`` into a ``None``. That None
    became a 400 "No task store found." — a fixed sentence that replaced
    whatever the store actually said, so the one message carrying the diagnosis
    ("stamped for a DIFFERENT store", "canonical store ... does not exist", the
    export/COUNT(*) disagreement) was thrown away at the door and the operator
    got a generic banner instead. The caller now renders the real reason; see
    :func:`api_dispatch`.
    """
    return get_board(_tasks_path_from_request(request), allow_stale=allow_stale)


@csrf_exempt
def api_dispatch(request, endpoint):
    """Dispatch ``/<endpoint>`` to its handler function."""
    handler = HANDLERS.get(endpoint)
    if handler is None:
        return JsonResponse({"error": f"Unknown endpoint: {endpoint}"}, status=404)

    if endpoint in NO_BOARD_ENDPOINTS:
        return handler(request, None)

    # A STORE THAT CANNOT BE READ IS A 500 CARRYING ITS OWN REASON. Two failure
    # shapes converge here and both were unreadable before: a swallowed
    # FileNotFoundError became a generic 400, and every OTHER load failure
    # (notably the ownership refusal) escaped this function entirely, because
    # this call sat OUTSIDE the try below — so Django answered with an HTML
    # error page that the board's ``fetch`` cannot parse, and the frontend
    # showed a bare "HTTP 500" with no cause. The board template already reads
    # ``payload.error`` off a non-OK response and renders it in the loud red
    # panel, so putting the store's own sentence in the body is what turns an
    # outage into a diagnosis. NEVER answer this with an empty board.
    try:
        board = _get_board(
            request,
            allow_stale=(endpoint in STALE_OK_ENDPOINTS and request.method == "GET"),
        )
    except Exception as exc:
        logger.exception("[scitex-todo] cannot read the task store for /%s", endpoint)
        # THE FULL SENTENCE STAYS FOR US AND GOES NOWHERE ELSE.
        #
        # The comment above is right that the store's own sentence is what turns
        # an outage into a diagnosis, and that stays true on the loopback board.
        # But scitex-hub loaded /apps/cards/ ANONYMOUSLY in a browser and got the
        # whole paragraph, including the absolute container path
        # /app/.scitex/cards/cards.db. A stranger learns our filesystem layout and
        # reads a rationale addressed to us.
        #
        # settings.DEBUG is the switch, and it is already correct on both sides
        # with no new configuration: the local board runs DEBUG=true and keeps the
        # diagnosis, while SCITEX_CARDS_PUBLIC_HOST FORCES DEBUG=false (settings.py,
        # deliberately not env-overridable), so anything publicly reachable gets the
        # summary. A second flag would be a second thing to set wrongly.
        #
        # The log line above is unconditional, so the detail is never lost - it
        # moves from the response body to the place that was always the right home
        # for it.
        from django.conf import settings

        public = getattr(exc, "public_summary", None)
        if public is not None and not settings.DEBUG:
            return JsonResponse({"error": public}, status=500)
        return JsonResponse({"error": f"Cannot read the task store: {exc}"}, status=500)

    try:
        return handler(request, board)
    except Exception as exc:
        logger.exception("[scitex-todo] API error on /%s", endpoint)
        return JsonResponse({"error": str(exc)}, status=500)


# EOF
