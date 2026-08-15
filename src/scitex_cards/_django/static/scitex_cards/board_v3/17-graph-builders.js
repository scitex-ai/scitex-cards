/* 17-graph-builders.js — the PURE string builders behind the board_v3 Graph
 * layout: task set -> mermaid source, and the two HTML fragments that frame
 * the diagram.
 *
 * Extracted VERBATIM from board_v3.html's inline <script> (the C10 cluster) as
 * split PR-3, following 15-dateinfo.js and 16-card-helpers.js
 * (card scitex-cards-gui-board-v3-template-split-20260717). The bodies below
 * are the inline originals character-for-character, including the template
 * literals and their indentation — the emitted HTML must not shift, and
 * retyping it as string concatenation is how that happens by accident.
 *
 * Publishes window.STX.graphBuilders.
 *
 * WHAT DELIBERATELY STAYED IN THE TEMPLATE, and why this file is worth having:
 * _mountGraphSvg, toggleGraphFit, _renderGraphSvg, _ensureMermaid,
 * _renderGraphView and _prewarmGraph all touch the DOM, localStorage, or the
 * network. The rule this split follows (from scitex-cards-chat's #470) is that
 * a module reading `document` at IMPORT time cannot be require()d by node, so
 * it stays untestable even after it moves out of the template — moving the DOM
 * code would have been churn, not testability. Only the pure half moved.
 *
 * THE TWO PAGE GLOBALS ARE NOW PARAMETERS, which is the one change of
 * substance. Inline, `_graphSrc` read STATE.graph and `_graphHintHtml` read
 * GRAPH_FIT straight off the page; a function whose output depends on ambient
 * mutable state cannot be tested by calling it. They arrive as arguments now,
 * so every case in the test file is a plain call with a plain expected value.
 * The template passes exactly what it used to read, so behaviour is unchanged.
 */
"use strict";

(function (global) {
  // Pure: task set + the /graph payload -> mermaid source (or null when
  // nothing is connected). `graph` is what the template holds in STATE.graph.
  function _graphSrc(visible, graph) {
    const visibleIds = new Set(visible.map(t => t.id));
    const _esc = (s) => String(s).replace(/[^A-Za-z0-9_-]/g, "_");
    const _safe = (s) => String(s || "").replace(/[[\]"]/g, " ").slice(0, 60);

    // Pull EDGES from the canonical server payload, not per-node.
    const rawEdges = (graph && graph.edges) || [];
    const depEdges = [];
    for (const e of rawEdges) {
      // `kind` is "depends_on" or "blocks"; the server already emits
      // source=dep, target=tid (handlers/graph.py:121) so the arrow
      // points prereq → consumer either way.
      if (!e || !visibleIds.has(e.source) || !visibleIds.has(e.target)) continue;
      depEdges.push({ src: e.source, dst: e.target, kind: e.kind || "depends_on" });
    }
    // Hierarchical `parent` edges are a separate axis (not in `edges[]`).
    // Dashed so hierarchy reads differently from a blocking dependency.
    const parentEdges = [];
    for (const t of visible) {
      const p = t.parent;
      if (p && visibleIds.has(p) && p !== t.id) {
        parentEdges.push({ src: p, dst: t.id, kind: "parent" });
      }
    }
    const allEdges = depEdges.concat(parentEdges);
    const touched = new Set();
    for (const e of allEdges) { touched.add(e.src); touched.add(e.dst); }
    const connected = visible.filter(t => touched.has(t.id));
    if (!connected.length) return null;

    // Each node carries its RAW status class (st-<status>) so the in-board
    // mermaid matches the python build_mermaid() artifacts — no 4-bucket
    // color collapse.
    const nodeLines = connected.map(t => {
      const label = `${(t.project || "—").slice(0, 18)}<br>${_safe(t.task || t.title)}`;
      return `  ${_esc(t.id)}["${label}"]:::st-${(t.status || "deferred").replace(/[^a-z_]/gi, "")}`;
    });
    const edgeLines = allEdges.map(e => (e.kind === "parent")
      ? `  ${_esc(e.src)} -.-> ${_esc(e.dst)}`
      : `  ${_esc(e.src)} --> ${_esc(e.dst)}`);
    // classDefs from the SSOT status_colors (STATUS_STYLE, projected into
    // the /graph payload) — never a second color table.
    const sc = (graph && graph.status_colors) || {};
    const classDefLines = Object.keys(sc).map(s => {
      const c = sc[s];
      let style = `fill:${c.fill},stroke:${c.stroke},stroke-width:1px,color:#222`;
      if (c.dashed) style += ",stroke-dasharray:5 3";
      return `  classDef st-${s} ${style};`;
    });
    const src = ["flowchart LR", ...classDefLines, ...nodeLines, ...edgeLines]
      .join("\n");
    return {
      src,
      stats: {
        connected: connected.length,
        dep: depEdges.length,
        parent: parentEdges.length,
        hidden: visible.length - connected.length,
      },
    };
  }

  // The counts line above the diagram, plus the fit/1:1 toggle button.
  // `fit` is the template's GRAPH_FIT; it only picks the button label. The
  // onclick still names the template's toggleGraphFit, which owns the DOM +
  // localStorage half of the toggle and deliberately did not move here.
  function _graphHintHtml(stats, note, fit) {
    const s = stats;
    return `<div class="graph-hint">
        <span>Graph: <strong>${s.connected}</strong> connected node${s.connected === 1 ? "" : "s"} /
        <strong>${s.dep}</strong> depends_on / blocks edge${s.dep === 1 ? "" : "s"} /
        <strong>${s.parent}</strong> parent edge${s.parent === 1 ? "" : "s"} (dashed).
        ${s.hidden} disconnected card${s.hidden === 1 ? "" : "s"} not drawn.${note ? " " + note : ""}</span>
        <button type="button" class="graph-fit-btn" onclick="toggleGraphFit()"
                title="Fit the diagram to the panel width, or show it at 1:1 and scroll">
          ${fit ? "⤢ 1:1" : "⤡ Fit"}
        </button>
      </div>`;
  }

  // Shown when the filters admit cards but none of them are connected — an
  // explanation of what this layout draws, rather than a blank panel.
  function _graphEmptyHtml(visibleCount) {
    return `<div class="graph-wrap">
        <div class="graph-empty">
          <h3>No dependencies in the current scope</h3>
          <p>The Graph layout draws <code>depends_on</code> / <code>blocks</code>
          edges (from the server's <code>edges[]</code> payload) plus
          <code>parent</code> edges (per-node) among the currently-visible cards.
          Right now <strong>${visibleCount} card${visibleCount === 1 ? "" : "s"}</strong>
          match the filters but none of them are connected to each other.</p>
          <p class="graph-empty__hint">Encode dependencies in <code>tasks.yaml</code> like this:
          <code>depends_on: [other-task-id]</code>, <code>blocks: [downstream-id]</code>,
          or <code>parent: hub-task-id</code>. The <strong>⏱ Timeline</strong> and
          <strong>🗒 Wall</strong> layouts show every card, connected or not.</p>
        </div>
      </div>`;
  }

  const _api = {
    _graphSrc: _graphSrc,
    _graphHintHtml: _graphHintHtml,
    _graphEmptyHtml: _graphEmptyHtml,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.graphBuilders = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
