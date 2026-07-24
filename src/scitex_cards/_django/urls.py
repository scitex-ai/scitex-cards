#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL patterns for the scitex-todo board Django app."""

from django.urls import path

from . import views
from .handlers.chat import chat_view
from .handlers.dm import dm_thread_view, dm_threads_view
from .handlers.fleet import (
    fleet_ci_status_view,
    fleet_timing_view,
)
from .handlers.hooks import hook_done_view, hook_push_view
from .handlers.runnable import blocked_batch_view, runnable_view
from .handlers.timeline import timeline_view

app_name = "scitex_cards"

urlpatterns = [
    # Fleet dashboard — Phase 1 surface (CI-status pills strip). The
    # registry-reader harness lives in ``handlers/fleet/`` and is the
    # template subsequent waves (hosts, mesh, timing, chat) plug into.
    # Registered BEFORE the catch-all ``<path:endpoint>`` route so the
    # slashed path is matched cleanly instead of getting routed to
    # ``api_dispatch`` (which would 404 — no handler named
    # "fleet/ci-status"). The fleet surfaces are intentionally
    # namespaced under ``/fleet/`` so future panels sit next to it.
    path("fleet/ci-status", fleet_ci_status_view, name="fleet_ci_status"),
    # Fleet dashboard — Phase 4 surface (timing telemetry). Operator
    # ask (TG, relayed by lead a2a `74db4f2d` + `10afa799`,
    # 2026-06-14): "record what took how long → self-improvement". The
    # endpoint derives per-task durations (created_to_started,
    # started_to_done, created_to_done) from the timestamps the task
    # store already carries (`created_at` + `_log_meta.started_at` +
    # `_log_meta.completed_at`) and aggregates them per agent /
    # project / group (median + p95) over a sliding window
    # (`?window_days=30` default). The Phase-5 chart UI consumes this
    # payload. Registered BEFORE the catch-all `<path:endpoint>` route
    # so the slashed path is matched cleanly instead of getting routed
    # to `api_dispatch` (which would 404).
    path("fleet/timing", fleet_timing_view, name="fleet_timing"),
    # T1.4 (lead a2a `74db4f2d`, 2026-06-14) — TRACK-1 dispatch backbone
    # HTTP surface. /runnable returns the FULL runnable set per the
    # T1.2 `runnable_tasks` predicate; /blocked-batch returns the
    # inverse view per T1.3. The lead-side parallelism dispatcher
    # consumes these instead of shelling out to the CLI verbs.
    path("runnable", runnable_view, name="runnable"),
    path("blocked-batch", blocked_batch_view, name="blocked_batch"),
    # Time View — operator-direct ask (TG, relayed by lead a2a `d0f7a0e3`,
    # 2026-06-14). Live raster timeline so the operator watches ONE screen
    # and sees the whole fleet in motion. Polled by the FE TimelineView
    # every 30s (same cadence as the CI-status pills). Registered BEFORE
    # the catch-all ``<path:endpoint>`` route for the same reason as the
    # other named GET endpoints — otherwise ``api_dispatch`` would 404.
    path("timeline", timeline_view, name="timeline"),
    # Fleet dashboard — Phase 6 surface (CHAT). Operator↔agent thread
    # view sitting on top of the existing per-card ``comments[]``
    # substrate. Lead a2a `74db4f2d` + `10afa799` greenlight; last of
    # the 6 TRACK-2 surfaces. GET returns the card's comments[]; POST
    # appends one (delegates to ``_store.comment_task``). Registered
    # BEFORE the catch-all ``<path:endpoint>`` route so the slashed
    # path is matched cleanly instead of getting routed to
    # ``api_dispatch`` (which would 404 — no handler named "chat/<id>").
    path("chat/<str:card_id>", chat_view, name="chat"),
    # Operator↔agent DIRECT-MESSAGE surface (scitex-dev DM convention v1;
    # card fleet-agent-direct-message-board-pane-20260707). `/chat` (no
    # trailing segment — distinct from the per-card `/chat/<card_id>`
    # comment thread above) serves the mobile-first DM page; the two
    # `/dm/*` JSON endpoints back its agent list + thread pane + compose.
    # Registered BEFORE the catch-all `<path:endpoint>` route so the
    # slashed paths are matched cleanly instead of 404ing in api_dispatch.
    path("chat", views.chat_page, name="chat_page"),
    # ...and the SLASHED spelling, exactly as `legacy/` and `board-v3/` do above.
    # Without it `/chat/` matches neither `chat` nor `chat/<card_id>` (a str
    # converter will not match an empty segment), falls through to the catch-all
    # and answers `{"error": "Unknown endpoint: chat/"}`. The operator hit that
    # on 2026-07-24 the first time they opened the page: a trailing slash is the
    # most natural thing in the world to type, and it 404'd a page that exists.
    # `chat_page` already strips BOTH aliases off request.path when deriving the
    # include root, so the slashed form was mount-aware before it was reachable.
    path("chat/", views.chat_page, name="chat_page_slash"),
    path("dm/threads", dm_threads_view, name="dm_threads"),
    path("dm/thread/<str:peer>", dm_thread_view, name="dm_thread"),
    # Hook-consumer endpoints (lead a2a `6fff33d6` + `fbffb879`,
    # 2026-06-14, operator-mandated). Loose-coupling contract for
    # SAC's push-hook + dev's merge-Action to record progress / DONE
    # on the board. POST-only, idempotent. Built-in handlers +
    # entry-point plugin dispatch in
    # ``scitex_cards._hooks.dispatch_event``. The entry-point group
    # external producers register under is ``scitex_cards.hooks``.
    path("hooks/push", hook_push_view, name="hook_push"),
    path("hooks/done", hook_done_view, name="hook_done"),
    # ROOT = the operator-approved v3 layout. Operator TG 263 confirmed
    # post-screenshot: "はい、root においてください。http://127.0.0.1:8051/".
    # Lead-coordinated promotion per a2a `62094366` — once v3 was proven
    # on real seeded data + the operator green-lit it visually, swap the
    # root from the React-SPA GraphView to the server-rendered board_v3.
    path("", views.board_v3_page, name="board"),
    # The previous root view (React SPA GraphView built by Vite) moves to
    # `/legacy/` as a backup escape hatch — preserved (not deleted) so any
    # tool or muscle memory pointing at the old layout still has access.
    # If the operator decides v3 fully replaces the legacy board, this
    # entry can be dropped in a later PR.
    path("legacy", views.board_page, name="board_legacy"),
    path("legacy/", views.board_page, name="board_legacy_slash"),
    # `/board-v3/` alias retained for short-period back-compat (lead +
    # bookmarks may still hit it). Serves the same view as root.
    path("board-v3", views.board_v3_page, name="board_v3"),
    path("board-v3/", views.board_v3_page, name="board_v3_slash"),
    # `/favicon.ico` must precede the catch-all `<path:endpoint>` route — the
    # browser requests it implicitly and the catch-all would otherwise route
    # it to api_dispatch (→ 404). favicon_view serves the bundled SVG.
    path("favicon.ico", views.favicon_view, name="favicon"),
    path("<path:endpoint>", views.api_dispatch, name="api"),
]

# EOF
