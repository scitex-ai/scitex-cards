/* timeline.js — board_v3 "Timeline" layout (operator CARD 2026-06-17).
 *
 * An own-data layout (sibling of the Stale layout) with three views,
 * chosen via a selector, over a day / week / month window:
 *
 *   • By Agent   — SVG time-raster, one row per agent, bars = task spans.
 *   • By Project — same raster, rows keyed by the task's project.
 *   • Simple     — a RICH per-task list (operator's "1b" choice): each task
 *                  is a card showing its status + latest comment INLINE
 *                  (the "communication space"), positioned newest-first.
 *
 * The two rasters pull GET /timeline?window_hours=N&lane_by=agent|project
 * (the server projects lanes + bar geometry). The simple list projects
 * STATE.graph.nodes by time client-side so it can show comments (which the
 * /timeline wire payload omits).
 *
 * Loaded as a classic <script defer> AFTER board_v3's inline extra_js, so
 * it shares the page globals (STATE, escapeHtml, openDetail, render,
 * toast). It exposes its entry points on `window` so the inline
 * render() dispatch + the generated onclick handlers can reach them.
 *
 * No build step — served static like searchQuery.js / recentSort.js.
 */
"use strict";

(function () {
  // D/W/M → window_hours. Published on STX so Details > Stats uses this SAME
  // definition of a day/week/month, not a second one that can drift.
  var WINDOWS = ((globalThis.STX = globalThis.STX || {}).timelineWindows =
    { "1d": 24, "1w": 168, "1m": 720 });

  // Layout constants for the raster SVG.
  var LANE_H = 26; // px floor per lane row (a 1-row lane keeps this height)
  var BAR_INSET = 4; // vertical padding inside a lane
  var LABEL_W = 124; // lane-label gutter width (tightened 150→124 to give the
  //                    plot more of the left side; labels truncate to 22 chars
  //                    which fit in ~120px at the 11px bold lane-label font)
  var AXIS_H = 22; // top time-axis height
  var TICKS = 6; // axis tick count (incl. both ends)
  var SUB_ROW_H = 20; // px per beeswarm sub-row inside a lane (18→20 so the
  //                     larger r=7 dots + selected ring don't collide)
  var SUB_ROW_GAP = 2; // px gap two markers need to share one sub-row
  var MAX_ROWS = 12; // cap on sub-rows per lane (overflow clamps)

  // Deterministic beeswarm sub-row packer (pure; lives in timelinePack.js so
  // this file stays under the line cap and the algorithm is node-testable).
  // Falls back to a single-row no-op if the sibling script hasn't loaded.
  var _pack =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.timelinePack &&
      globalThis.STX.timelinePack.packRows) ||
    function (items) {
      return { rows: new Array(items.length).fill(0), rowCount: 1 };
    };

  // Anti-flash gate (sig + scroll snapshot; lives in timelineGate.js, same
  // line-cap + node-test reasons as _pack). Skips the canvas.innerHTML rebuild
  // when an identical ~4s /timeline auto-refresh would otherwise flash the
  // raster + reset scroll; preserves scroll on a real (changed) rebuild.
  var _G =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.timelineGate) ||
    null;
  var _gate = _G ? _G.makeGate() : null;
  var _sig =
    (_G && _G.rasterSig) ||
    function () {
      return "no-gate:" + Date.now();
    };

  // Persisted view + window selections (mirror STATE.sort/layout stickiness).
  function _ls(key, dflt) {
    try {
      return localStorage.getItem(key) || dflt;
    } catch (e) {
      return dflt;
    }
  }
  var TL = {
    cache: null, // last /timeline payload (raster views only)
    error: null,
    view: _ls("scitex-cards:tl-view", "agent"), // agent | project | simple
    windowKey: _ls("scitex-cards:tl-window", "1d"), // 1d | 1w | 1m
  };
  // Expose for the inline autoRefresh hook (read-only use there).
  window._TL = TL;

  // ── pure geometry (lives in timelineGeo.js, captured here like _pack) ──
  // ms / barGeo / makeTicks / relTime: time→pixel projection used below.
  var _geo =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.timelineGeo) ||
    {};
  var ms =
    _geo.ms ||
    function () {
      return null;
    };
  var barGeo =
    _geo.barGeo ||
    function () {
      return null;
    };
  var makeTicks =
    _geo.makeTicks ||
    function () {
      return [];
    };
  var relTime =
    _geo.relTime ||
    function () {
      return "";
    };

  // ── controls row (shared by all three views) ─────────────────────────
  // Built in timelineControls.js (captured here like _pack/_geo so this
  // file stays under the per-file cap). That sibling appends the
  // dependency-edge legend for the raster views and self-wires the
  // hover-highlight delegation on #columns. Fallback keeps the page usable
  // (selects-only, no legend) if the sibling hasn't loaded.
  var _ctl =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.timelineControls) ||
    null;
  // Per-agent lane avatar (10-agent-avatar.js); null until wired in.
  var _avatar =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.agentAvatar) ||
    null;
  function controlsHtml(countLabel) {
    if (_ctl && _ctl.controlsHtml)
      return _ctl.controlsHtml(TL, countLabel, escapeHtml);
    return (
      '<div class="tl-controls"><span class="tl-count">' +
      escapeHtml(countLabel) +
      "</span></div>"
    );
  }

  // ── data: raster views fetch /timeline ───────────────────────────────
  function loadTimeline() {
    var hrs = WINDOWS[TL.windowKey] || 24;
    var laneBy = TL.view === "project" ? "project" : "agent";
    fetch((window.API_BASE || "") + "/timeline?window_hours=" + hrs + "&lane_by=" + laneBy)
      .then(function (r) {
        if (!r.ok)
          return r
            .json()
            .catch(function () {
              return { error: r.statusText };
            })
            .then(function (b) {
              throw new Error(b.error || "timeline " + r.status);
            });
        return r.json();
      })
      .then(function (payload) {
        TL.cache = payload;
        TL.error = null;
        if (STATE.layout === "timeline" && TL.view !== "simple") render();
      })
      .catch(function (e) {
        TL.error = (e && e.message) || String(e);
        if (STATE.layout === "timeline" && TL.view !== "simple") render();
      });
  }

  // ── raster view (agent / project) ────────────────────────────────────
  function renderRaster(canvas) {
    if (TL.cache === null && !TL.error) {
      canvas.innerHTML =
        '<div class="tl-wrap">' +
        controlsHtml("loading…") +
        '<div class="loading">loading timeline…</div></div>';
      loadTimeline();
      return;
    }
    // Anti-flash gate: if the view/window/payload are byte-identical to what
    // we last rendered, skip the rebuild entirely so the ~4s auto-refresh
    // doesn't flash the raster or reset the operator's scroll. A real change
    // (or a user-driven view/window switch, which resets the gate) falls
    // through and rebuilds — preserving scroll across the innerHTML swap.
    var sig = _sig(TL.view, TL.windowKey, TL.cache, TL.error);
    if (_gate && _gate.unchanged(sig)) return;
    var p = TL.cache || { events: [], edges: [], lanes: [] };
    var ws = ms(p.window_start);
    var we = ms(p.window_end);
    var now = Date.now();
    var lanes = p.lanes || [];
    var width = Math.max(
      320,
      (canvas.getBoundingClientRect().width || 900) - LABEL_W - 28,
    );
    // index events by lane for stable row order
    var byLane = {};
    lanes.forEach(function (l) {
      byLane[l] = [];
    });
    (p.events || []).forEach(function (ev) {
      if (byLane[ev.lane]) byLane[ev.lane].push(ev);
    });

    // SCATTER: ONE dot per task at its start time within its lane. Each lane
    // runs a deterministic beeswarm packer (packRows) so co-located markers
    // fan out into sub-rows instead of occluding. Lanes therefore have
    // VARIABLE height + a CUMULATIVE top — walk `cursor` from AXIS_H.
    var dots = [];
    var dotById = {};
    var laneTops = []; // y of each lane's top edge (aligned to `lanes`)
    var laneHeights = []; // each lane's px height
    var cursor = AXIS_H;
    lanes.forEach(function (lane, li) {
      var geos = []; // {ev, x, w} per visible event (x = clamped start px)
      (byLane[lane] || []).forEach(function (ev) {
        var g = barGeo(ms(ev.started_at), ms(ev.ended_at), ws, we, now, width);
        if (g) geos.push({ ev: ev, x: g.x, w: g.width });
      });
      var packed = _pack(geos, SUB_ROW_GAP, MAX_ROWS);
      var laneH = Math.max(LANE_H, Math.max(1, packed.rowCount) * SUB_ROW_H);
      laneTops[li] = cursor;
      laneHeights[li] = laneH;
      geos.forEach(function (it, k) {
        var dot = {
          cx: LABEL_W + it.x,
          cy: cursor + (packed.rows[k] || 0) * SUB_ROW_H + SUB_ROW_H / 2,
          ev: it.ev,
          lane: lane,  // carried onto the dot so row-hover can grow it
        };
        dots.push(dot);
        dotById[it.ev.id] = dot;
      });
      cursor += laneH;
    });
    var total = cursor + 6;
    var ticks = makeTicks(ws, we, width, TICKS);
    // "By Agent" rows get the avatar (10-agent-avatar.js) once wired in.
    var isAgentLane = TL.view !== "project";
    var laneStatus =
      _avatar && _avatar.laneStatusMap
        ? _avatar.laneStatusMap(lanes, byLane, ms)
        : {};

    // axis
    var svg = "";
    svg += '<g class="tl-axis">';
    ticks.forEach(function (tk) {
      var x = LABEL_W + tk.x;
      svg +=
        '<line class="tl-tickline" x1="' +
        x +
        '" x2="' +
        x +
        '" y1="' +
        (AXIS_H - 4) +
        '" y2="' +
        total +
        '"></line>' +
        '<text class="tl-ticktext" x="' +
        x +
        '" y="' +
        (AXIS_H - 8) +
        '" text-anchor="middle">' +
        escapeHtml(tk.label) +
        "</text>";
    });
    svg += "</g>";
    svg += '<g class="tl-lanes">';
    lanes.forEach(function (lane, i) {
      var yTop = laneTops[i];
      var laneH = laneHeights[i];
      var cy = yTop + laneH / 2;
      // data-lane = the row-hover hit target (13-timeline-hover.js).
      svg +=
        '<rect class="tl-lane-bg' +
        (i % 2 === 0 ? " tl-lane-bg--even" : "") +
        '" data-lane="' + escapeHtml(String(lane)) +
        '" x="0" y="' + yTop +
        '" width="' + (LABEL_W + width) +
        '" height="' + laneH +
        '"></rect>';
      svg += _avatar
        ? _avatar.laneLabelSvg(cy, lane, laneStatus[lane], isAgentLane)
        : '<text class="tl-lane-label" x="8" y="' +
          (cy + 4) +
          '">' +
          escapeHtml(lane) +
          "</text>";
    });
    svg += "</g>";
    // dependency lines (drawn before the dots) — connect dot centres
    svg += '<g class="tl-edges">';
    (p.edges || []).forEach(function (e) {
      var s = dotById[e.source];
      var t = dotById[e.target];
      if (!s || !t) return;
      svg +=
        '<line class="tl-edge tl-edge--' +
        (e.kind === "blocks" ? "blocks" : "depends") +
        '" data-source="' +
        escapeHtml(String(e.source)) +
        '" data-target="' +
        escapeHtml(String(e.target)) +
        '" x1="' +
        s.cx +
        '" y1="' +
        s.cy +
        '" x2="' +
        t.cx +
        '" y2="' +
        t.cy +
        '"></line>';
    });
    svg += "</g>";
    // dots — ONE per task. Click → drawer; hover → the offset tip from
    // 12-hover-tip.js. data-lane lets a row-hover grow the lane's dots.
    svg += '<g class="tl-dots">';
    dots.forEach(function (d) {
      var done = d.ev.ended_at != null;
      var title =
        d.ev.title +
        "\nstatus: " +
        d.ev.status +
        (d.ev.started_at ? "\nstarted: " + d.ev.started_at : "") +
        (d.ev.ended_at ? "\ncompleted: " + d.ev.ended_at : "");
      svg +=
        '<circle class="tl-dot' +
        (done ? " tl-dot--done" : " tl-dot--live") +
        '" data-lane="' + escapeHtml(String(d.lane || "")) +
        '" data-card-id="' +
        escapeHtml(String(d.ev.id)) +
        '" data-status="' +
        escapeHtml(String(d.ev.status || "")) +
        '" cx="' +
        d.cx +
        '" cy="' +
        d.cy +
        '" r="7" data-tip="' +
        escapeHtml(title) +
        '" onclick="openDetail(\'' +
        escapeHtml(String(d.ev.id)) +
        "')\"></circle>";
    });
    svg += "</g>";

    var count = (p.events || []).length + " events";
    // Snapshot the .tl-scroll offsets (overflow:auto → both axes scroll there)
    // BEFORE the innerHTML swap recreates that element, then restore after so
    // a real data change doesn't yank the operator back to the top/left.
    if (_gate) _gate.snapshot(canvas);
    canvas.innerHTML =
      '<div class="tl-wrap">' +
      controlsHtml(count) +
      (lanes.length
        ? '<div class="tl-scroll"><svg class="tl-svg" width="' +
          (LABEL_W + width) +
          '" height="' +
          total +
          '" role="img" aria-label="Fleet timeline raster">' +
          svg +
          "</svg></div>"
        : '<div class="loading">no activity in this window 🌙</div>') +
      "</div>";
    if (_gate) {
      _gate.restore(canvas);
      _gate.mark(sig);
    }
  }

  // ── simple view (rich per-task cards from STATE.graph) ───────────────
  function renderSimple(canvas) {
    var nodes = (STATE.graph && STATE.graph.nodes) || [];
    var now = Date.now();
    var cutoff = now - (WINDOWS[TL.windowKey] || 24) * 3600 * 1000;
    var rows = nodes
      .map(function (t) {
        var act = ms(t.last_activity) || ms(t.created_at);
        return { t: t, act: act };
      })
      .filter(function (r) {
        return r.act != null && r.act >= cutoff;
      })
      .sort(function (a, b) {
        return b.act - a.act;
      });

    var cards = rows
      .map(function (r) {
        var t = r.t;
        var cs = Array.isArray(t.comments) ? t.comments : []; // fallback only
        var last = t.last_comment || (cs.length ? cs[cs.length - 1] : null);
        var commentHtml = last
          ? '<div class="tl-card-comment"><span class="tl-card-comment-author">' +
            escapeHtml(last.author || "?") +
            "</span> " +
            escapeHtml(_truncate(last.text_preview || last.text || "", 160)) +
            "</div>"
          : '<div class="tl-card-comment tl-card-comment--none">no comments yet</div>';
        var meta = [
          t.project ? escapeHtml(t.project) : null,
          t.agent ? "@" + escapeHtml(t.agent) : null,
          t.priority ? "#" + escapeHtml(String(t.priority)) : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          '<div class="tl-card" data-status="' +
          escapeHtml(String(t.status || "")) +
          '" onclick="openDetail(\'' +
          escapeHtml(String(t.id)) +
          "')\">" +
          '<div class="tl-card-top">' +
          '<span class="tl-card-status">' +
          escapeHtml(t.status || "") +
          "</span>" +
          '<span class="tl-card-time">' +
          escapeHtml(relTime(r.act, now)) +
          "</span>" +
          "</div>" +
          '<div class="tl-card-title">' +
          escapeHtml(t.title || t.task || t.id) +
          "</div>" +
          (meta ? '<div class="tl-card-meta">' + meta + "</div>" : "") +
          commentHtml +
          (comments.length
            ? '<div class="tl-card-count">💬 ' + comments.length + "</div>"
            : "") +
          "</div>"
        );
      })
      .join("");

    canvas.innerHTML =
      '<div class="tl-wrap">' +
      controlsHtml(rows.length + " tasks") +
      (rows.length
        ? '<div class="tl-simple">' + cards + "</div>"
        : '<div class="loading">no activity in this window 🌙</div>') +
      "</div>";
  }

  function _truncate(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  // ── entry point (called by the inline render() dispatch) ─────────────
  function renderTimeline(canvas) {
    if (TL.view === "simple") renderSimple(canvas);
    else renderRaster(canvas);
  }

  // ── control setters (called by the generated <select> onchange) ──────
  function setTimelineView(v) {
    TL.view = v;
    if (_gate) _gate.reset(); // user-driven change → force a redraw
    try {
      localStorage.setItem("scitex-cards:tl-view", v);
    } catch (e) {}
    if (v === "simple") render();
    else {
      TL.cache = null;
      loadTimeline();
      render();
    }
  }
  function setTimelineWindow(k) {
    TL.windowKey = k;
    if (_gate) _gate.reset(); // user-driven change → force a redraw
    try {
      localStorage.setItem("scitex-cards:tl-window", k);
    } catch (e) {}
    if (TL.view === "simple") render();
    else {
      TL.cache = null;
      loadTimeline();
      render();
    }
  }

  // Called by the inline autoRefreshTick on a store change so the raster
  // refreshes live (the simple view re-renders from STATE.graph anyway).
  function timelineOnStoreChange() {
    if (STATE.layout === "timeline" && TL.view !== "simple") loadTimeline();
  }

  // Auto dynamic update (operator 2026-06-17): keep the timeline fresh +
  // FLOWING as time passes. A self-gating timer re-fetches the raster (its
  // window is now-relative, so dots drift leftward each tick) or re-renders
  // the simple list — ONLY while the Timeline layout is active. On by
  // default, no toggle. Skips while a dot/card is hovered so the tooltip the
  // operator is reading isn't yanked out from under them.
  var TL_LIVE_MS = 5000;
  setInterval(function () {
    if (typeof STATE === "undefined" || !STATE || STATE.layout !== "timeline")
      return;
    if (document.querySelector(".tl-dot:hover, .tl-card:hover")) return;
    if (TL.view === "simple") render();
    else loadTimeline();
  }, TL_LIVE_MS);

  // Publish the entry points the inline board code + onclick handlers use.
  window._renderTimelineView = renderTimeline;
  window.setTimelineView = setTimelineView;
  window.setTimelineWindow = setTimelineWindow;
  window.loadTimeline = loadTimeline;
  window.timelineOnStoreChange = timelineOnStoreChange;
})();
