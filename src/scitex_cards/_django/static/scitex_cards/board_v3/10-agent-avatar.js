/* 10-agent-avatar.js — per-agent lane-label avatar (brand icon / initials)
 * for the Timeline "By Agent" raster (timeline.js) and the Wall's agent
 * tiles (11-sticky-wall.js).
 *
 * Operator 2026-07-10 (cards-board-agent-icons-status-rings-20260710):
 * tracing left across the raster lane-by-lane to find the owning agent is
 * slow. This gives each agent lane a small circular icon — the fleet brand
 * colour + short label, or 2-letter initials on a deterministic hash-derived
 * hue for agents outside the brand map.
 *
 * STATUS RING REMOVED 2026-07-13 (operator: "エージェントアイコンの周りが何を
 * 表すのかよくわかりませんでした。エージェントアイコンの周りの状態マーカはなくして
 * 良いかなと思います"). The ring encoded the status of the lane's most recently
 * STARTED event — a fact that is already right there in the lane's own dots,
 * and one nobody could read off a ring. `avatarSvg()` keeps its `status`
 * parameter (call sites are unchanged, and laneStatusMap() is still exported
 * for anything else that wants the derivation) but no longer draws it.
 *
 * Pure (no DOM reads, no fetch) — node-testable, same shape as the sibling
 * timelinePack.js / timelineGeo.js. Published on window.STX.agentAvatar;
 * timeline.js captures it at init exactly like it captures _pack/_geo/_ctl,
 * falling back to the plain-text lane label when this script hasn't
 * loaded (see timeline.js's `_avatar` capture).
 *
 * MUST load BEFORE timeline.js (script order in board_v3.html) for the
 * capture to see it on first render — that wiring line is the one piece
 * NOT done here (see PR report: board_v3.html was out of scope for this
 * change).
 */
"use strict";

(function (global) {
  // board_v3.html's inline script defines escapeHtml on window BEFORE this
  // deferred script runs; the String() fallback only matters for node tests.
  var _esc =
    global.escapeHtml ||
    function (x) {
      return String(x == null ? "" : x);
    };
  // Mirrors timeline.js's own `_truncate` (same "…"-ellipsis semantics) —
  // duplicated here rather than passed in so this module has zero
  // dependency on timeline.js's internals.
  function _truncate(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }
  // FNV-ish rolling hash → stable hue per id. Deterministic across
  // reloads/hosts since it only depends on the id string's char codes.
  function hueFor(id) {
    var s = String(id == null ? "" : id);
    var h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h) % 360;
  }

  // Fleet brand map — SAME identity system as the Telegram bot avatars the
  // operator already recognises (claude-code-telegrammer
  // docs/icons/generate_bot_icons.py: solid brand colour + short white
  // label; exact hexes for cct/writer/figrecipe/dsp come from that file,
  // the rest are matched to the live avatars: Hub=blue, CARD=teal,
  // SAC=green, NV=purple, Grant=orange, DEV=slate, pClew=teal-green).
  // Agents NOT in this map fall back to hash-hue + initials, so a new
  // agent still gets a stable, distinct icon with zero config.
  var BRAND = {
    "scitex-cards": { label: "CARDS", color: "#1f8a70" },
    "scitex-hub": { label: "Hub", color: "#2f6fd0" },
    "scitex-dev": { label: "DEV", color: "#5a6472" },
    "scitex-agent-container": { label: "SAC", color: "#2fae5f" },
    "grant": { label: "Grant", color: "#e8963e" },
    "scitex-writer": { label: "Writer", color: "#5865c9" },
    "neurovista": { label: "NV", color: "#7d4fd3" },
    "paper-scitex-clew": { label: "pClew", color: "#22a08a" },
    "claude-code-telegrammer": { label: "CCT", color: "#1a2a40" },
    "figrecipe": { label: "Fig", color: "#d97742" },
    "scitex-dsp": { label: "DSP", color: "#6c8ba0" },
  };
  function brandFor(id) {
    return BRAND[String(id == null ? "" : id)] || null;
  }

  // "worker-telegrammer-orochi" -> "WO", "ywata-note-win" -> "YW",
  // single-token ids (e.g. "orochi") -> first two chars "OR".
  function initialsFor(id) {
    var s = String(id == null ? "" : id).trim();
    if (!s) return "?";
    var parts = s.split(/[^a-zA-Z0-9]+/).filter(Boolean);
    if (parts.length >= 2)
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return s.slice(0, 2).toUpperCase() || "?";
  }

  // <g> with a hash-hued (or brand-coloured) fill circle + centred label.
  // `status` is accepted but IGNORED since 2026-07-13 — see the file header:
  // the status ring around the icon was unreadable, so it is not drawn.
  function avatarSvg(cx, cy, r, id, status) {  // eslint-disable-line no-unused-vars
    // Brand-mapped agents render with their fleet colour + short label (the
    // identity the operator already knows from the Telegram avatars);
    // unknown agents keep the deterministic hash-hue + initials fallback.
    var brand = brandFor(id);
    var fill = brand ? brand.color : "hsl(" + hueFor(id) + ",55%,42%)";
    var label = brand ? brand.label : initialsFor(id);
    var textCls =
      "tl-avatar-text" + (label.length > 3 ? " tl-avatar-text--sm" : "");
    return (
      '<g class="tl-avatar">' +
      '<circle class="tl-avatar-bg" cx="' +
      cx +
      '" cy="' +
      cy +
      '" r="' +
      r +
      '" style="fill:' +
      fill +
      '"></circle>' +
      '<text class="' +
      textCls +
      '" x="' +
      cx +
      '" y="' +
      (cy + 3) +
      '" text-anchor="middle">' +
      _esc(label) +
      "</text>" +
      "</g>"
    );
  }

  // lane -> status of its most-recently-started event, given the raster's
  // `lanes` array + `byLane` index (lane -> events[]) + its `ms` time-parse
  // fn. Lives here (not timeline.js) purely to keep timeline.js under the
  // repo's 512-line-per-file cap — see PR report.
  function laneStatusMap(lanes, byLane, msFn) {
    var out = {};
    (lanes || []).forEach(function (lane) {
      var bestEv = null;
      var bestT = -1;
      (byLane[lane] || []).forEach(function (ev) {
        var t = msFn(ev.started_at) || 0;
        if (t >= bestT) {
          bestT = t;
          bestEv = ev;
        }
      });
      out[lane] = bestEv ? bestEv.status : null;
    });
    return out;
  }

  // The full lane-label markup for one row: the avatar+ring group + the
  // truncated name (shifted right of it) when `isAgentLane`, else the
  // plain (untruncated-gutter) text label as today — the single call site
  // timeline.js needs so it doesn't have to duplicate this branching.
  function laneLabelSvg(cy, lane, status, isAgentLane) {
    if (!isAgentLane) {
      return (
        '<text class="tl-lane-label" x="8" y="' +
        (cy + 4) +
        '">' +
        _esc(_truncate(lane, 22)) +
        "</text>"
      );
    }
    var avR = 9;
    var avCx = 8 + avR;
    return (
      avatarSvg(avCx, cy, avR, lane, status) +
      '<text class="tl-lane-label" x="' +
      (avCx + avR + 6) +
      '" y="' +
      (cy + 4) +
      '">' +
      _esc(_truncate(lane, 12)) +
      "</text>"
    );
  }

  global.STX = global.STX || {};
  global.STX.agentAvatar = {
    hueFor: hueFor,
    initialsFor: initialsFor,
    avatarSvg: avatarSvg,
    laneStatusMap: laneStatusMap,
    laneLabelSvg: laneLabelSvg,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
