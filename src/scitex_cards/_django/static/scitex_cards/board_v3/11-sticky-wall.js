/* 11-sticky-wall.js — "sticky note wall" layout for board_v3.
 *
 * Operator design (cards-board-sticky-wall-view-20260710, msgs 929/931/933):
 *   - Timeline is the centrepiece, but hover-to-read is tedious: every note
 *     must be READABLE WITHOUT HOVER.
 *   - Cluster the notes "like UMAP, but 適当に" — by a plain key (assignee by
 *     default), no embedding.
 *   - Selecting an assignee surfaces THAT AGENT'S NEXT-UP STACK on top.
 *   - His worry: needing agents to maintain that stack ("エージェント頼みに
 *     なるのがだるい"). They don't: the stack is DERIVED here from the graph
 *     the board already ships (nodes + depends_on edges). No agent
 *     cooperation, no nudges, no new API.
 *   - The board is planned to become a product; polish is product surface.
 *
 * Pure + node-testable (same shape as timelinePack.js / 10-agent-avatar.js).
 * Publishes window.STX.stickyWall. Consumed by board_v3.html's render()
 * dispatch; falls back to the column view when absent.
 */
"use strict";

(function (global) {
  var _esc =
    global.escapeHtml ||
    function (x) {
      return String(x == null ? "" : x);
    };

  var TERMINAL = { done: 1, failed: 1, cancelled: 1 };

  function _clamp(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  // Group key for a node. `by` ∈ assignee | project | status.
  function groupKeyOf(node, by) {
    if (by === "project") return node.project || node.repo || "(no project)";
    if (by === "status") return node.status || "(none)";
    return node.agent || node.assignee || "(unassigned)";
  }

  // Upstreams that are NOT resolved block a card. Mirrors the server's
  // RESOLVED_STATUSES = {done, goal} (see _runnable.py) — a cancelled or
  // failed upstream does NOT satisfy a dependency, so it still blocks.
  var RESOLVED = { done: 1, goal: 1 };

  /* Runnable = open, not `blocked`, and every depends_on upstream resolved.
   * Derived ENTIRELY from what /graph already returns — this is the piece
   * that removes the operator's agent-dependence worry. */
  function runnableSet(nodes, edges) {
    var statusById = {};
    (nodes || []).forEach(function (n) {
      statusById[n.id] = n.status;
    });
    var blockedBy = {};
    (edges || []).forEach(function (e) {
      // depends_on: e.source depends on e.target. blocks: e.source blocks
      // e.target — the same relation read the other way.
      var dependent = e.kind === "blocks" ? e.target : e.source;
      var upstream = e.kind === "blocks" ? e.source : e.target;
      if (e.kind !== "depends_on" && e.kind !== "blocks") return;
      if (!RESOLVED[statusById[upstream]]) {
        (blockedBy[dependent] = blockedBy[dependent] || []).push(upstream);
      }
    });
    var out = {};
    (nodes || []).forEach(function (n) {
      if (TERMINAL[n.status]) return;
      if (n.status === "blocked") return;
      if (blockedBy[n.id] && blockedBy[n.id].length) return;
      out[n.id] = true;
    });
    return out;
  }

  function _prio(n) {
    var p = n.priority;
    return typeof p === "number" ? p : 99;
  }

  /* Next-up ordering: runnable first, then priority asc, then most recent.
   * Stable and deterministic — same data, same stack. */
  function nextUpSort(nodes, runnable) {
    return (nodes || []).slice().sort(function (a, b) {
      var ra = runnable[a.id] ? 0 : 1;
      var rb = runnable[b.id] ? 0 : 1;
      if (ra !== rb) return ra - rb;
      var pa = _prio(a);
      var pb = _prio(b);
      if (pa !== pb) return pa - pb;
      var la = String(a.last_activity || a.created_at || "");
      var lb = String(b.last_activity || b.created_at || "");
      return lb < la ? -1 : lb > la ? 1 : 0;
    });
  }

  function ageLabel(node, nowMs) {
    var ts = node.last_activity || node.created_at;
    if (!ts) return "";
    var t = Date.parse(String(ts));
    if (!t || isNaN(t)) return "";
    var h = (nowMs - t) / 3600000;
    if (h < 1) return Math.max(1, Math.round(h * 60)) + "m";
    if (h < 24) return Math.round(h) + "h";
    return Math.round(h / 24) + "d";
  }

  /* One note. Readable with NO hover: 2-line title, status as the left edge,
   * age + priority chips. Click -> the existing detail pane.
   *
   * The per-note agent icon was REMOVED 2026-07-13 (operator: "Wall では
   * カードごとにアイコンがあって冗長なので、エージェントのタイルの層にアイコンを
   * 置いてもらうのが良いかと思います"): when the wall is grouped by assignee every
   * note in an island repeats the island's own owner, so the icon carried
   * zero information N times. It now sits ONCE on the island header
   * (islandHtml below). Grouped by project/status the island key is not an
   * agent, so no icon is drawn there at all rather than a misleading one. */
  function noteHtml(node, opts) {
    opts = opts || {};
    var st = node.status || "none";
    var pr = typeof node.priority === "number" ? node.priority : null;
    var chips =
      (opts.isNext ? '<span class="sw-chip sw-chip--next">NEXT</span>' : "") +
      (pr !== null ? '<span class="sw-chip">P' + _esc(pr) + "</span>" : "") +
      (opts.age ? '<span class="sw-chip sw-chip--age">' + _esc(opts.age) + "</span>" : "");
    return (
      '<button type="button" class="sw-note sw-note--' +
      _esc(st) +
      '" data-id="' +
      _esc(node.id) +
      '" data-status="' +
      _esc(st) +
      // data-tip, not title= — the native tooltip renders UNDER the cursor
      // and hides the note you are pointing at (operator msg 944).
      // 12-hover-tip.js draws an offset one instead.
      '" data-tip="' +
      _esc(node.title || node.id) +
      '">' +
      '<span class="sw-note-head">' +
      '<span class="sw-note-chips">' +
      chips +
      "</span></span>" +
      '<span class="sw-note-title">' +
      _esc(_clamp(node.title || node.id, 84)) +
      "</span>" +
      "</button>"
    );
  }

  /* Island = one cluster. Area grows with count (the "poor-man's UMAP":
   * deterministic packing, related things adjacent, no embedding). */
  /* The island (agent tile) header icon — the ONE place an agent icon
   * belongs on the Wall. Only drawn when the island IS an agent, i.e. the
   * wall is grouped by assignee. */
  function islandAvatarHtml(key, by) {
    if (by !== "assignee") return "";
    if (!(global.STX && global.STX.agentAvatar)) return "";
    return (
      '<svg class="sw-island-av" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">' +
      global.STX.agentAvatar.avatarSvg(12, 12, 10, key, null) +
      "</svg>"
    );
  }

  function islandHtml(key, nodes, opts) {
    opts = opts || {};
    var selected = opts.selected === key;
    var runnable = opts.runnable || {};
    var nowMs = opts.nowMs || Date.now();
    var ordered = nextUpSort(nodes, runnable);
    // Collapsed islands show a bounded preview; the selected one shows all,
    // next-up first.
    var shown = selected ? ordered : ordered.slice(0, opts.previewCount || 6);
    var notes = shown
      .map(function (n, i) {
        return noteHtml(n, {
          isNext: selected && i === 0 && runnable[n.id],
          age: ageLabel(n, nowMs),
        });
      })
      .join("");
    var more = ordered.length - shown.length;
    return (
      '<section class="sw-island' +
      (selected ? " sw-island--selected" : "") +
      '" data-key="' +
      _esc(key) +
      '" style="--sw-count:' +
      ordered.length +
      '">' +
      '<header class="sw-island-head"><button type="button" class="sw-island-title" data-key="' +
      _esc(key) +
      '">' +
      islandAvatarHtml(key, opts.groupBy) +
      _esc(_clamp(key, 28)) +
      '<span class="sw-island-count">' +
      ordered.length +
      "</span></button></header>" +
      '<div class="sw-island-notes">' +
      notes +
      (more > 0 ? '<span class="sw-more">+' + more + " more</span>" : "") +
      "</div>" +
      "</section>"
    );
  }

  function group(nodes, by) {
    var m = {};
    (nodes || []).forEach(function (n) {
      var k = groupKeyOf(n, by);
      (m[k] = m[k] || []).push(n);
    });
    return m;
  }

  /* Full wall. `visible` is the already-filtered node set from render(). */
  function wallHtml(visible, edges, state) {
    state = state || {};
    var by = state.groupBy || "assignee";
    var runnable = runnableSet(visible, edges);
    var nowMs = state.nowMs || Date.now();
    var groups = group(visible, by);
    var keys = Object.keys(groups).sort(function (a, b) {
      var d = groups[b].length - groups[a].length;
      return d !== 0 ? d : a < b ? -1 : 1;
    });
    var islands = keys
      .map(function (k) {
        return islandHtml(k, groups[k], {
          selected: state.selected,
          runnable: runnable,
          nowMs: nowMs,
          previewCount: state.previewCount || 6,
          groupBy: by,   // the island key is an AGENT only when grouped by assignee
        });
      })
      .join("");
    return (
      '<div class="sw-wall" data-group-by="' +
      _esc(by) +
      '">' +
      (islands || '<p class="sw-empty">No cards match the current filters.</p>') +
      "</div>"
    );
  }

  global.STX = global.STX || {};
  global.STX.stickyWall = {
    groupKeyOf: groupKeyOf,
    runnableSet: runnableSet,
    nextUpSort: nextUpSort,
    ageLabel: ageLabel,
    noteHtml: noteHtml,
    islandHtml: islandHtml,
    wallHtml: wallHtml,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
