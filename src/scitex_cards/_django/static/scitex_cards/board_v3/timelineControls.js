/* timelineControls.js — Timeline-layout chrome + edge interaction for
 * board_v3 (operator 2026-06-26: "explain the dependency lines + let me
 * trace them"). Two cohesive concerns, both factored OUT of timeline.js
 * (which sits at the per-file line cap, same reason as timelinePack.js /
 * timelineGeo.js / timelineGate.js / timelineSelect.js):
 *
 *   1. controlsHtml(TL, esc) — the shared controls row (View/Window
 *      selects + count + error) used by all three timeline views, PLUS a
 *      small edge-key legend appended for the agent/project rasters
 *      (the simple list has no edges). The swatches are inline-SVG <line>s
 *      carrying the SAME .tl-edge--depends / .tl-edge--blocks classes as
 *      the real edges, so the key stays single-sourced with 09-timeline.css
 *      and flips with the dark/light theme.
 *
 *   2. hover-highlight — when the operator hovers a task DOT (or an edge),
 *      brighten the connected edges + neighbour dots and dim everything
 *      else so the dependency subgraph stands out. Wired via ONE
 *      mouseover/mouseout pair delegated on #columns, so it survives the
 *      raster's ~5s innerHTML auto-rebuilds WITHOUT timeline.js re-attaching
 *      anything. Reads neighbours from data-card-id (dots) and
 *      data-source / data-target (edges) that timeline.js now emits.
 *
 * Loaded as a classic <script defer> BEFORE timeline.js; publishes
 * window.STX.timelineControls (captured by timeline.js's IIFE at init).
 * The controlsHtml / edgeLegendHtml builders are pure + node-testable and
 * are also exported via module.exports (mirrors the other timeline*
 * siblings). No build step — served static.
 */
"use strict";

(function () {
  // ── pure: edge-key legend HTML ──────────────────────────────────────
  // `esc` is the page's escapeHtml (passed in; falls back to a no-op so the
  // pure helper is callable from node tests without the page global).
  function edgeLegendHtml(esc) {
    esc =
      esc ||
      function (s) {
        return String(s == null ? "" : s);
      };
    function swatch(kind, label) {
      return (
        '<span class="tl-edge-legend-item" title="' +
        esc(label) +
        '">' +
        '<svg class="tl-edge-legend-swatch" width="22" height="8" ' +
        'aria-hidden="true" focusable="false">' +
        '<line class="tl-edge tl-edge--' +
        kind +
        '" x1="1" y1="4" x2="21" y2="4"></line>' +
        "</svg>" +
        '<span class="tl-edge-legend-label">' +
        esc(label) +
        "</span></span>"
      );
    }
    return (
      '<span class="tl-edge-legend" role="img" ' +
      'aria-label="Edge key: a dotted grey line means depends on, ' +
      'a solid red line means blocks">' +
      swatch("depends", "depends on") +
      swatch("blocks", "blocks") +
      "</span>"
    );
  }

  // ── pure: the shared controls row ───────────────────────────────────
  // TL is timeline.js's view-state object {view, windowKey, error}; esc is
  // escapeHtml. setTimelineView / setTimelineWindow are window globals that
  // timeline.js publishes, referenced here only as inline-onchange strings.
  //
  // The Window control is a Day/Week/Month TAB group (operator 2026-10-20),
  // not a <select>: the board's underline-tab idiom (Board|DM switcher) is
  // the fleet shape, and a select reads as a toy-like inline switch next to
  // it. Same windowKey contract — only the chrome changed.
  var WINDOW_TABS = [
    ["1d", "Day"],
    ["1w", "Week"],
    ["1m", "Month"],
  ];
  function windowTabs(cur) {
    return (
      '<span class="tl-window-tabs" role="tablist" aria-label="Time window">' +
      WINDOW_TABS.map(function (kv) {
        var on = kv[0] === cur;
        return (
          '<button type="button" role="tab" class="tl-window-tab' +
          (on ? " tl-window-tab--on" : "") +
          '" data-window="' +
          kv[0] +
          '" aria-selected="' +
          (on ? "true" : "false") +
          "\" onclick=\"setTimelineWindow('" +
          kv[0] +
          "')\">" +
          kv[1] +
          "</button>"
        );
      }).join("") +
      "</span>"
    );
  }
  var VIEW_TABS = [
    ["agent", "By Agent"],
    ["project", "By Project"],
    ["simple", "Simple"],
  ];
  function viewTabs(cur) {
    return (
      '<span class="tl-view-tabs" role="tablist" aria-label="Timeline view">' +
      VIEW_TABS.map(function (kv) {
        var on = kv[0] === cur;
        return (
          '<button type="button" role="tab" class="tl-window-tab' +
          (on ? " tl-window-tab--on" : "") +
          '" data-view="' +
          kv[0] +
          '" aria-selected="' +
          (on ? "true" : "false") +
          "\" onclick=\"setTimelineView('" +
          kv[0] +
          "')\">" +
          kv[1] +
          "</button>"
        );
      }).join("") +
      "</span>"
    );
  }
  function controlsHtml(TL, countLabel, esc) {
    TL = TL || {};
    esc =
      esc ||
      function (s) {
        return String(s == null ? "" : s);
      };
    var err = TL.error
      ? '<span class="tl-error" title="' +
        esc(TL.error) +
        '">! ' +
        esc(TL.error) +
        "</span>"
      : "";
    // The edge key only makes sense for the agent/project rasters — the
    // simple per-task list draws no edges, so omit it there.
    var legend = TL.view === "simple" ? "" : edgeLegendHtml(esc);
    return (
      '<div class="tl-controls">' +
      '<span class="tl-ctl">View ' +
      viewTabs(TL.view) +
      "</span>" +
      '<span class="tl-ctl">Window ' +
      windowTabs(TL.windowKey) +
      "</span>" +
      '<span class="tl-count">' +
      esc(countLabel) +
      "</span>" +
      legend +
      err +
      "</div>"
    );
  }

  // Publish the pure helpers early (page + node).
  var _api = { controlsHtml: controlsHtml, edgeLegendHtml: edgeLegendHtml };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelineControls = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
    return; // node import path: no DOM wiring
  }
  if (typeof document === "undefined") return;

  // ── hover-highlight (DOM; delegated on #columns) ────────────────────
  function _isTimeline() {
    return (
      typeof window.STATE !== "undefined" &&
      window.STATE &&
      window.STATE.layout === "timeline"
    );
  }
  function _host() {
    return document.getElementById("columns");
  }

  // The dot's stable id. Prefer the explicit data-card-id timeline.js now
  // emits; fall back to the inline openDetail('<id>') onclick so this keeps
  // working even if a render path forgets the attribute.
  function _dotId(el) {
    if (!el) return null;
    if (el.dataset && el.dataset.cardId != null) return el.dataset.cardId;
    var oc = el.getAttribute && el.getAttribute("onclick");
    var m = oc && oc.match(/openDetail\((['"])(.*?)\1\)/);
    return m ? m[2] : null;
  }

  function _clear(svg) {
    if (!svg) return;
    svg.classList.remove("tl--highlighting");
    svg.querySelectorAll(".tl-edge--hot, .tl-dot--hot").forEach(function (el) {
      el.classList.remove("tl-edge--hot", "tl-dot--hot");
    });
  }

  // Highlight the subgraph touching a hovered DOT: every edge whose
  // source/target is this dot, plus the dots at the far ends.
  function _highlightDot(svg, dot) {
    var id = _dotId(dot);
    if (id == null) return;
    id = String(id);
    var neighbours = {};
    neighbours[id] = true;
    svg.querySelectorAll(".tl-edge").forEach(function (edge) {
      var s = edge.getAttribute("data-source");
      var t = edge.getAttribute("data-target");
      if (String(s) === id || String(t) === id) {
        edge.classList.add("tl-edge--hot");
        neighbours[String(s)] = true;
        neighbours[String(t)] = true;
      }
    });
    svg.querySelectorAll(".tl-dot").forEach(function (d) {
      var did = _dotId(d);
      if (did != null && neighbours[String(did)])
        d.classList.add("tl-dot--hot");
    });
    svg.classList.add("tl--highlighting");
  }

  // BONUS: hovering an EDGE highlights it + its two endpoint dots.
  function _highlightEdge(svg, edge) {
    var s = edge.getAttribute("data-source");
    var t = edge.getAttribute("data-target");
    edge.classList.add("tl-edge--hot");
    svg.querySelectorAll(".tl-dot").forEach(function (d) {
      var did = _dotId(d);
      if (
        did != null &&
        (String(did) === String(s) || String(did) === String(t))
      )
        d.classList.add("tl-dot--hot");
    });
    svg.classList.add("tl--highlighting");
  }

  function _onOver(e) {
    if (!_isTimeline() || !e.target || !e.target.closest) return;
    var svg = e.target.closest(".tl-svg");
    if (!svg) return; // hovering controls/legend/etc — leave any state alone
    var dot = e.target.closest(".tl-dot");
    if (dot) {
      _clear(svg);
      _highlightDot(svg, dot);
      return;
    }
    var edge = e.target.closest(".tl-edge");
    if (edge) {
      _clear(svg);
      _highlightEdge(svg, edge);
      return;
    }
    // Hovering raster background (lane stripe / axis / svg gaps) inside the
    // SVG → no marker under the cursor, so drop any active highlight.
    _clear(svg);
  }

  function _onOut(e) {
    if (!e.target || !e.target.closest) return;
    var svg = e.target.closest(".tl-svg");
    if (!svg) return;
    // Only clear when the cursor truly leaves the SVG (relatedTarget is
    // outside it); intra-SVG moves are handled by _onOver re-evaluating.
    var to = e.relatedTarget;
    if (to && to.closest && to.closest(".tl-svg") === svg) return;
    _clear(svg);
  }

  function _install() {
    var host = _host() || document;
    // Bubbling phase (default) — does NOT touch timeline.js's inline onclick
    // or timelineSelect.js's capture-phase click/dblclick/contextmenu, so
    // open-detail + multi-select are unaffected. Idempotent guard so a
    // double-install (load race) can't double-bind.
    if (host.__tlHoverWired) return;
    host.__tlHoverWired = true;
    host.addEventListener("mouseover", _onOver, false);
    host.addEventListener("mouseout", _onOut, false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _install);
  } else {
    _install();
  }
})();
