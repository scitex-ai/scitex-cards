/* 17-graph-builders.js — the PURE string builders behind the board_v3 Graph
 * layout: task set -> mermaid source, the HTML fragments that frame the
 * diagram, and the two predicates that decide whether a diagram is drawable
 * at all and whether one was actually drawn.
 *
 * Extracted from board_v3.html's inline <script> (the C10 cluster) as split
 * PR-3, following 15-dateinfo.js and 16-card-helpers.js (card
 * scitex-cards-gui-board-v3-template-split-20260717). The bodies arrived here
 * verbatim; the repo's eslint --fix has since reflowed them, but the template
 * literals are untouched, which is the part that matters — the emitted HTML
 * must not shift, and retyping it as string concatenation is how that happens
 * by accident. The 52 cases in test_graph_builders.py pin the emitted strings.
 *
 * WHY THIS FILE GREW A CAP AND TWO PREDICATES (2026-08-15, card
 * board-graph-layout-exceeds-mermaid-maxtextsize-20260815): the extraction
 * immediately paid for itself by making a live defect measurable. On the real
 * board, _graphSrc emitted 155,977 characters for 407 connected cards against
 * mermaid's 50,000-character ceiling, and mermaid answered by RETURNING AN
 * ERROR IMAGE AS A SUCCESSFUL RENDER. So the panel showed a pink error box
 * while every automated signal — a resolved promise, an empty console, a
 * correct-looking counts line — reported health. Both halves are fixed here:
 * the diagram is capped to a legible subset that SAYS it is a subset, and the
 * render path now asserts something about what came back instead of trusting
 * that a returned SVG is a drawn diagram.
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
  // mermaid 10 refuses any diagram whose source exceeds this many characters
  // and returns an ERROR IMAGE rather than throwing. The template pins the
  // same number in mermaid.initialize() so this constant is true by
  // construction rather than by matching whatever the library defaults to.
  const MERMAID_MAX_TEXT = 50000;
  // How many connected cards the diagram will draw. The real board reached
  // 407 connected nodes / 155,977 characters of source — 3.1x over the limit
  // above — and silently showed an error box for weeks. A cap is not a
  // workaround for that: 407 nodes of dagre layout is unreadable even when it
  // renders, so the honest diagram is a legible subset that SAYS it is one.
  const GRAPH_NODE_CAP = 120;

  // How many neighbours one seed may claim before the next seed gets a turn.
  // Without it the largest hub eats the entire budget — measured on the live
  // board, where one 300-child epic filled all 120 slots and the diagram
  // showed a single star instead of the board's shape.
  const GRAPH_SEED_SHARE = 12;

  // Pick at most `cap` of the connected cards, keeping neighbourhoods intact.
  //
  // Taking the top-`cap` nodes by degree alone would be a trap: a hub with 50
  // children has degree 50 while every child has degree 1, so a degree ranking
  // selects hubs and drops every one of their neighbours — leaving a diagram
  // of isolated dots. So we walk the degree order as SEEDS and pull each
  // seed's neighbours in with it, which yields whole stars.
  //
  // Two passes: the first gives every seed at most GRAPH_SEED_SHARE
  // neighbours so several neighbourhoods make it in, the second spends
  // whatever budget is left deepening them in the same order. Deterministic
  // throughout: ties break on id, so the same board always draws the same
  // subset rather than a different one each repaint.
  function _selectDrawn(connected, allEdges, cap) {
    if (!cap || cap <= 0 || connected.length <= cap) {
      return { nodes: connected, edges: allEdges };
    }
    const adj = new Map();
    for (const t of connected) adj.set(t.id, []);
    for (const e of allEdges) {
      if (adj.has(e.src) && adj.has(e.dst)) {
        adj.get(e.src).push(e.dst);
        adj.get(e.dst).push(e.src);
      }
    }
    const byId = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    const deg = (id) => (adj.get(id) || []).length;
    const rank = (a, b) => deg(b) - deg(a) || byId(a, b);
    const seeds = connected.map((t) => t.id).sort(rank);
    const keep = new Set();
    for (const share of [GRAPH_SEED_SHARE, Infinity]) {
      for (const seed of seeds) {
        if (keep.size >= cap) break;
        const nbrs = (adj.get(seed) || [])
          .filter((n) => !keep.has(n))
          .sort(rank);
        // A seed admitted with no room left for a neighbour would be drawn as
        // an isolated dot and then dropped again below — it would have spent
        // a slot to show nothing.
        if (!keep.has(seed) && nbrs.length && keep.size + 2 > cap) continue;
        keep.add(seed);
        let taken = 0;
        for (const nbr of nbrs) {
          if (keep.size >= cap || taken >= share) break;
          keep.add(nbr);
          taken++;
        }
      }
    }
    const edges = allEdges.filter((e) => keep.has(e.src) && keep.has(e.dst));
    // A node whose every edge went to a dropped neighbour is disconnected in
    // the drawn subset, and the layout's own rule is that disconnected nodes
    // are not drawn. Dropping it here keeps the reported count honest.
    const linked = new Set();
    for (const e of edges) {
      linked.add(e.src);
      linked.add(e.dst);
    }
    return { nodes: connected.filter((t) => linked.has(t.id)), edges };
  }

  // Pure: task set + the /graph payload -> mermaid source (or null when
  // nothing is connected). `graph` is what the template holds in STATE.graph.
  // `cap` limits how many connected cards are drawn (default GRAPH_NODE_CAP;
  // 0 or negative draws every one of them).
  function _graphSrc(visible, graph, cap) {
    const visibleIds = new Set(visible.map((t) => t.id));
    const _esc = (s) => String(s).replace(/[^A-Za-z0-9_-]/g, "_");
    const _safe = (s) =>
      String(s || "")
        .replace(/[[\]"]/g, " ")
        .slice(0, 60);

    // Pull EDGES from the canonical server payload, not per-node.
    const rawEdges = (graph && graph.edges) || [];
    const depEdges = [];
    for (const e of rawEdges) {
      // `kind` is "depends_on" or "blocks"; the server already emits
      // source=dep, target=tid (handlers/graph.py:121) so the arrow
      // points prereq → consumer either way.
      if (!e || !visibleIds.has(e.source) || !visibleIds.has(e.target))
        continue;
      depEdges.push({
        src: e.source,
        dst: e.target,
        kind: e.kind || "depends_on",
      });
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
    for (const e of allEdges) {
      touched.add(e.src);
      touched.add(e.dst);
    }
    const connected = visible.filter((t) => touched.has(t.id));
    if (!connected.length) return null;

    // What we will actually draw. `connected` stays the honest denominator
    // for the hint line; everything below works from the selected subset.
    const picked = _selectDrawn(
      connected,
      allEdges,
      cap === undefined || cap === null ? GRAPH_NODE_CAP : cap,
    );
    const drawn = picked.nodes;
    const drawnEdges = picked.edges;
    if (!drawn.length) return null;

    // Each node carries its RAW status class (st-<status>) so the in-board
    // mermaid matches the python build_mermaid() artifacts — no 4-bucket
    // color collapse.
    const nodeLines = drawn.map((t) => {
      const label = `${(t.project || "—").slice(0, 18)}<br>${_safe(t.task || t.title)}`;
      return `  ${_esc(t.id)}["${label}"]:::st-${(t.status || "deferred").replace(/[^a-z_]/gi, "")}`;
    });
    const edgeLines = drawnEdges.map((e) =>
      e.kind === "parent"
        ? `  ${_esc(e.src)} -.-> ${_esc(e.dst)}`
        : `  ${_esc(e.src)} --> ${_esc(e.dst)}`,
    );
    // classDefs from the SSOT status_colors (STATUS_STYLE, projected into
    // the /graph payload) — never a second color table.
    const sc = (graph && graph.status_colors) || {};
    const classDefLines = Object.keys(sc).map((s) => {
      const c = sc[s];
      let style = `fill:${c.fill},stroke:${c.stroke},stroke-width:1px,color:#222`;
      if (c.dashed) style += ",stroke-dasharray:5 3";
      return `  classDef st-${s} ${style};`;
    });
    const src = [
      "flowchart LR",
      ...classDefLines,
      ...nodeLines,
      ...edgeLines,
    ].join("\n");
    // The counts describe WHAT IS DRAWN, not what was considered: `dep` and
    // `parent` are the surviving edges, `connected` the surviving nodes.
    // `total` / `omitted` are what makes the cap visible instead of a silent
    // truncation — the hint line reports them.
    //
    // `nodes` is DISTINCT ESCAPED IDS, which is what mermaid will actually
    // draw and therefore the only honest number to measure a render against:
    // _esc maps every non-[A-Za-z0-9_-] character to "_", so two cards whose
    // ids differ only in punctuation collapse into one mermaid node. Counting
    // cards here instead would make _graphRenderFailed report a perfectly
    // good render as a failure on any board holding such a pair.
    const drawnIds = new Set(drawn.map((t) => _esc(t.id)));
    return {
      src,
      stats: {
        connected: drawn.length,
        nodes: drawnIds.size,
        dep: drawnEdges.filter((e) => e.kind !== "parent").length,
        parent: drawnEdges.filter((e) => e.kind === "parent").length,
        hidden: visible.length - connected.length,
        total: connected.length,
        omitted: connected.length - drawn.length,
      },
    };
  }

  // The counts line above the diagram, plus the fit/1:1 toggle button.
  // `fit` is the template's GRAPH_FIT; it only picks the button label. The
  // onclick still names the template's toggleGraphFit, which owns the DOM +
  // localStorage half of the toggle and deliberately did not move here.
  //
  // When the cap dropped cards it says SO AND BY HOW MANY. The line already
  // had the habit of reporting what it did not draw ("N disconnected cards
  // not drawn"); a cap that went unmentioned would be the one number on the
  // panel that lies.
  function _graphHintHtml(stats, note, fit) {
    const s = stats;
    const omitted = s.omitted || 0;
    const drawnCount = omitted
      ? `<strong>${s.connected}</strong> of <strong>${s.total}</strong> connected nodes`
      : `<strong>${s.connected}</strong> connected node${s.connected === 1 ? "" : "s"}`;
    const capNote = omitted
      ? ` ${omitted} more connected card${omitted === 1 ? " is" : "s are"} omitted to keep the diagram drawable — narrow the filters to see ${omitted === 1 ? "it" : "them"}.`
      : "";
    return `<div class="graph-hint">
        <span>Graph: ${drawnCount} /
        <strong>${s.dep}</strong> depends_on / blocks edge${s.dep === 1 ? "" : "s"} /
        <strong>${s.parent}</strong> parent edge${s.parent === 1 ? "" : "s"} (dashed).
        ${s.hidden} disconnected card${s.hidden === 1 ? "" : "s"} not drawn.${capNote}${note ? " " + note : ""}</span>
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

  // === Did mermaid actually draw the diagram we asked for? ===============
  //
  // THE DEFECT THIS CLOSES, as MEASURED against mermaid 10 in the browser on
  // 2026-08-15 rather than assumed:
  //
  //   source over the ceiling -> mermaid returns a PERFECTLY VALID
  //     flowchart-v2 SVG containing exactly ONE node, whose LABEL reads
  //     "Maximum text size in diagram exceeded". It does not throw, and the
  //     result carries no error markup of any kind.
  //   parse error -> mermaid.render() REJECTS, which the caller's catch
  //     already handles.
  //
  // So the failure that hit the live board arrives disguised as a successful
  // render of a one-node diagram. (The `.error-icon` / `.error-text` classes
  // that first looked like a signal are in the boilerplate <style> block of
  // EVERY mermaid diagram — matching them detects nothing at all. That was
  // the first version of this check, and probing the real library is what
  // showed it never fired.)
  //
  // Hence a MEASUREMENT, not a string match: we know how many nodes we asked
  // for, so a render that came back with fewer did not draw our diagram.
  // That catches the overflow case (1 node instead of 120), an empty result,
  // and whatever mermaid degrades to next — none of which need to be
  // enumerated in advance. The two error-markup patterns stay as a cheap
  // extra: a mermaid version that returns its error diagram instead of
  // throwing would otherwise have to be caught by the node count alone.
  const _NODE_GROUP_RE = /class\s*=\s*"[^"]*\bnode\b[^"]*"/g;
  const _ERROR_SVG_RE =
    /aria-roledescription\s*=\s*["']error["']|class\s*=\s*["'][^"']*\berror-(?:icon|text)\b/;
  function _graphRenderFailed(svg, expectedNodes) {
    const s = String(svg || "");
    if (!s) return true;
    if (_ERROR_SVG_RE.test(s)) return true;
    return (s.match(_NODE_GROUP_RE) || []).length < (expectedNodes || 0);
  }

  // Would mermaid refuse this source outright? Asked BEFORE handing it over,
  // so an oversized board gets an explanation rather than an error image.
  function _exceedsMermaidLimit(src, limit) {
    const max =
      limit === undefined || limit === null ? MERMAID_MAX_TEXT : limit;
    return String(src || "").length > max;
  }

  // The honest failure panel. `reason` names WHICH failure, because the two
  // are not the same problem and the operator can act on one of them: a
  // bundle that would not load is an offline/CDN problem, an error image is
  // mermaid refusing the diagram we built.
  function _graphFailureHtml(reason) {
    const why =
      reason === "render"
        ? `mermaid returned something other than the diagram it was asked
          for. That is a rendering failure, not a data problem — the cards
          themselves are fine. Narrowing the filters usually gets a diagram
          back.`
        : `the mermaid bundle failed to load (offline?). The card data is
          fine; try Timeline or Wall.`;
    return `<p class="graph-empty__hint">Could not draw the diagram — ${why}</p>`;
  }

  // Shown when the source we built is over mermaid's ceiling. Reports the
  // measurement rather than a vague "too big", so the next person does not
  // have to rediscover the numbers the way this bug had to be.
  function _graphOversizeHtml(stats, length, limit) {
    const max =
      limit === undefined || limit === null ? MERMAID_MAX_TEXT : limit;
    return `<div class="graph-wrap">
        <div class="graph-empty">
          <h3>Too much of the board to draw at once</h3>
          <p>The diagram for the current filters needs
          <strong>${length.toLocaleString("en-US")}</strong> characters of
          mermaid source against a ceiling of
          <strong>${max.toLocaleString("en-US")}</strong>, across
          <strong>${stats.total} connected card${stats.total === 1 ? "" : "s"}</strong>.
          Asking for it would come back as mermaid's own "maximum text size"
          notice rather than a graph, so it was not attempted.</p>
          <p class="graph-empty__hint">Narrow the filters — by project, status
          or search — and the diagram comes back. The <strong>⏱ Timeline</strong>
          and <strong>🗒 Wall</strong> layouts show every card, connected or
          not.</p>
        </div>
      </div>`;
  }

  const _api = {
    _graphSrc: _graphSrc,
    _graphHintHtml: _graphHintHtml,
    _graphEmptyHtml: _graphEmptyHtml,
    _graphRenderFailed: _graphRenderFailed,
    _exceedsMermaidLimit: _exceedsMermaidLimit,
    _graphFailureHtml: _graphFailureHtml,
    _graphOversizeHtml: _graphOversizeHtml,
    MERMAID_MAX_TEXT: MERMAID_MAX_TEXT,
    GRAPH_NODE_CAP: GRAPH_NODE_CAP,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.graphBuilders = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
