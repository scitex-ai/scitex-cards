/** Outer board component: load the graph, render header (progress + legend +
 * filter toolbar) and the React Flow canvas. */

import { useEffect, useMemo } from "react";
import { CalendarView } from "./CalendarView";
import { FleetCiPills } from "./FleetCiPills";
import { FleetTimingPanel } from "./FleetTimingPanel";
import { GraphView } from "./GraphView";
import { RecentView } from "./RecentView";
import { TableView } from "./TableView";
import { TimelineView } from "./TimelineView";
import { NodeDetailPanelContainer } from "./NodeDetailPanel";
import { ContextMenu } from "./ContextMenu";
import { EdgeKindMenu } from "./EdgeKindMenu";
import { AutoRefresh } from "./AutoRefresh";
import { DrillHistory } from "./DrillHistory";
import { Toast } from "./Toast";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import {
  EDGE_COLOR_BLOCKS,
  EDGE_COLOR_DEPENDS,
  nodeChildCount,
  partitionNodes,
} from "./layout";
import { taskMatchesFilter, useBoardStore } from "./store/useBoardStore";
import { parseSearchQuery } from "./searchQuery";
import { SearchAutocomplete } from "./SearchAutocomplete";
import { downloadText, toCsv, toJson, toMarkdown } from "./exportBoard";
import type { GraphPayload, StatusColor } from "./types/board";

/** Segmented toggle between the graph and the flat table view. */
function ViewToggle() {
  const view = useBoardStore((s) => s.view);
  const setView = useBoardStore((s) => s.setView);
  return (
    <div className="stx-todo-viewtoggle" role="group" aria-label="View mode">
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "graph" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("graph")}
        aria-pressed={view === "graph"}
      >
        Graph
      </button>
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "table" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("table")}
        aria-pressed={view === "table"}
      >
        Table
      </button>
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "recent" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("recent")}
        aria-pressed={view === "recent"}
        title="Recent — newest-first triage (operator TG 513)"
      >
        Recent
      </button>
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "calendar" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("calendar")}
        aria-pressed={view === "calendar"}
        title="Calendar — month grid by deadline / last_activity (operator TG 13295)"
      >
        📅 Calendar
      </button>
      {/* Time View — live raster timeline (operator-direct ask, TG;
       * relayed by lead a2a `d0f7a0e3`, 2026-06-14). 5th LAYOUT
       * alongside Graph / Table / Recent / Calendar. */}
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "timeline" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("timeline")}
        aria-pressed={view === "timeline"}
        title="Time View — live raster timeline of the whole fleet (operator-direct ask, lead a2a d0f7a0e3)"
      >
        ⏱ Time
      </button>
    </div>
  );
}

function Legend({ colors }: { colors: Record<string, StatusColor> }) {
  return (
    <div className="stx-todo-legend">
      {Object.entries(colors).map(([status, c]) => (
        <span key={status} className="stx-todo-legend__item">
          <span
            className="stx-todo-legend__swatch"
            style={{
              background: c.fill,
              border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
            }}
          />
          {status}
        </span>
      ))}
      {/* Edge semantics — depends_on (arrow) vs blocks (inhibition tee). */}
      <span className="stx-todo-legend__item" title="A depends on B">
        <span
          className="stx-todo-legend__edge"
          style={{ background: EDGE_COLOR_DEPENDS }}
        />
        depends&nbsp;on&nbsp;→
      </span>
      <span className="stx-todo-legend__item" title="A blocks B">
        <span
          className="stx-todo-legend__edge"
          style={{ background: EDGE_COLOR_BLOCKS }}
        />
        blocks&nbsp;⊣
      </span>
    </div>
  );
}

/** Per-status progress summary. Narrows to the current drill scope when one
 * is active (direct children of the scope), else counts the whole store.
 *
 * The "blocked N" chip is clickable (operator UX 2026-06-06): clicking it
 * toggles a filter to status=blocked so the operator can jump from "what is
 * the SHAPE of progress" to "show me the things that need unblocking" in one
 * click. Other status chips are passive (read-only display); blocking is the
 * one most worth a shortcut because acting on it unblocks the rest. */
function Progress({ graph }: { graph: GraphPayload }) {
  const drillPath = useBoardStore((s) => s.drillPath);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const toggleStatus = useBoardStore((s) => s.toggleStatus);
  const scope = drillPath.length ? drillPath[drillPath.length - 1] : null;
  const scoped = useMemo(
    () =>
      scope === null
        ? graph.nodes
        : graph.nodes.filter((n) => n.parent === scope),
    [graph.nodes, scope],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of scoped) c[n.status] = (c[n.status] ?? 0) + 1;
    return c;
  }, [scoped]);
  // "Awaiting operator" lens count (lead a2a `554435df` + `2bd37bd2`):
  // STRICT predicate — kind=="decision" AND status=="blocked" AND
  // blocker=="operator-decision". Do NOT dilute with transitive
  // dependents (those are reachable via the BlockersSection drill-in;
  // re-cluttering the lens defeats the whole anti-flood point).
  const awaitingOperator = useMemo(
    () =>
      scoped.filter(
        (n) =>
          n.kind === "decision" &&
          n.status === "blocked" &&
          n.blocker === "operator-decision",
      ).length,
    [scoped],
  );
  const total = scoped.length;
  const done = counts["done"] ?? 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  // Order the chips so the "alive" states read left→right.
  const order = [
    "goal",
    "in_progress",
    "blocked",
    "pending",
    "deferred",
    "failed",
    "done",
  ];
  const blockedOn = activeStatuses.includes("blocked");
  return (
    <span className="stx-todo-progress" aria-label="Progress summary">
      <strong>
        {scope ? "scope " : ""}
        {done}/{total} done ({pct}%)
      </strong>
      {awaitingOperator > 0 && (
        <span
          className="stx-todo-progress__chip stx-todo-progress__chip--awaiting-operator"
          title="Decision nodes awaiting the operator (kind=decision, status=blocked, blocker=operator-decision). Click a node to open its ADR (adr.md)."
        >
          👤 awaiting you {awaitingOperator}
        </span>
      )}
      {order
        .filter((s) => counts[s])
        .map((s) =>
          s === "blocked" ? (
            <button
              key={s}
              type="button"
              className={`stx-todo-progress__chip stx-todo-progress__chip--blocked${
                blockedOn ? " stx-todo-progress__chip--on" : ""
              }`}
              onClick={() => toggleStatus("blocked")}
              aria-pressed={blockedOn}
              title={
                blockedOn
                  ? "Clear the blocked-only filter"
                  : "Filter the board to blocked tasks only"
              }
            >
              🚧 blocked {counts[s]}
            </button>
          ) : (
            <span key={s} className="stx-todo-progress__chip">
              {s} {counts[s]}
            </span>
          ),
        )}
    </span>
  );
}

/** Inline hint pills rendered above the toolbar search input. Mirrors the
 * board_v3 (vanilla-template) hint-pill UX shipped with the
 * GitHub-style qualifier syntax (operator TG 12315 / 12316, lead a2a
 * 7dde227a, 2026-06-12). Empty unless the query contains a `<key>:`. */
function QualifierHints({ query }: { query: string }) {
  const parsed = useMemo(() => parseSearchQuery(query), [query]);
  if (!parsed.hasQualifiers) return null;
  return (
    <div
      className="stx-todo-toolbar__qhints"
      aria-live="polite"
      aria-label="Recognized search qualifiers"
    >
      {parsed.hints.map((h, i) => {
        const cls = [
          "stx-todo-toolbar__qhint",
          h.unknown ? "stx-todo-toolbar__qhint--unknown" : "",
          h.unknownValue ? "stx-todo-toolbar__qhint--unknown-value" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const tip = h.unknown
          ? `unknown qualifier — did you mean: ${h.suggestion}`
          : h.unknownValue
            ? `unknown value — try one of: ${h.suggestion}`
            : `filter on ${h.label}`;
        return (
          <span key={`${h.label}-${i}`} className={cls} title={tip}>
            <span className="stx-todo-toolbar__qhint-key">{h.label}:</span>
            <span className="stx-todo-toolbar__qhint-val">
              {h.value || "(empty)"}
            </span>
          </span>
        );
      })}
    </div>
  );
}

/** Search box + status-filter chips. Filters the graph (dim non-matches) and
 * the pool (hide non-matches) via the shared store filter. */
function Toolbar({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const setQuery = useBoardStore((s) => s.setQuery);
  const toggleStatus = useBoardStore((s) => s.toggleStatus);
  const setRepos = useBoardStore((s) => s.setRepos);
  const resetFilters = useBoardStore((s) => s.resetFilters);
  const statuses = Object.keys(graph.status_colors);
  // Distinct, sorted repos present on the board (for the facet chips).
  const repos = useMemo(() => {
    const set = new Set<string>();
    for (const n of graph.nodes) if (n.repo) set.add(n.repo);
    return [...set].sort();
  }, [graph.nodes]);
  const filtering =
    query.trim().length > 0 ||
    activeStatuses.length > 0 ||
    activeRepos.length > 0;

  return (
    <div className="stx-todo-toolbar">
      <SearchAutocomplete query={query} setQuery={setQuery} nodes={graph.nodes}>
        <input
          className="stx-todo-toolbar__search"
          type="search"
          placeholder="Search — try project:foo, status:blocked, kind:compute, …"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search tasks"
          title="Fuzzy match + GitHub-style qualifiers (project: / agent: / status: / kind: / parent: / scope: / id: / priority: / host:). Tab completes the qualifier or value under the cursor."
        />
      </SearchAutocomplete>
      <QualifierHints query={query} />
      <div className="stx-todo-toolbar__chips">
        {statuses.map((s) => {
          const on = activeStatuses.includes(s);
          const c = graph.status_colors[s];
          return (
            <button
              key={s}
              type="button"
              className={`stx-todo-chip${on ? " stx-todo-chip--on" : ""}`}
              style={
                on
                  ? { background: c.fill, borderColor: c.stroke, color: "#222" }
                  : { borderColor: c.stroke }
              }
              onClick={() => toggleStatus(s)}
              aria-pressed={on}
            >
              {s}
            </button>
          );
        })}
      </div>
      {repos.length > 0 && (
        <select
          className={`stx-todo-toolbar__repo${
            activeRepos.length ? " stx-todo-toolbar__repo--on" : ""
          }`}
          value={activeRepos[0] ?? ""}
          onChange={(e) => setRepos(e.target.value ? [e.target.value] : [])}
          aria-label="Filter by repo"
          title="Filter by repo"
        >
          <option value="">All repos ({repos.length})</option>
          {repos.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      )}
      {filtering && (
        <button
          type="button"
          className="stx-todo-toolbar__reset"
          onClick={resetFilters}
        >
          clear
        </button>
      )}
      <ExportGroup graph={graph} />
    </div>
  );
}

/** Export the currently-visible (filtered) tasks to a downloaded file. */
function ExportGroup({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const visible = useMemo(
    () =>
      graph.nodes.filter((n) =>
        taskMatchesFilter(n, query, activeStatuses, activeRepos),
      ),
    [graph.nodes, query, activeStatuses, activeRepos],
  );
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const base = `scitex-cards-${stamp}-${visible.length}`;
  return (
    <span
      className="stx-todo-toolbar__export"
      role="group"
      aria-label={`Export ${visible.length} visible tasks`}
      title={`Export ${visible.length} visible tasks`}
    >
      <span className="stx-todo-toolbar__export-label">Export</span>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() =>
          downloadText(
            toMarkdown(graph, visible),
            `${base}.md`,
            "text/markdown",
          )
        }
      >
        MD
      </button>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() =>
          downloadText(toCsv(graph, visible), `${base}.csv`, "text/csv")
        }
      >
        CSV
      </button>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() =>
          downloadText(
            toJson(graph, visible),
            `${base}.json`,
            "application/json",
          )
        }
      >
        JSON
      </button>
    </span>
  );
}

/** "Total · Showing · In nested parents · Pool" counter breakdown.
 *
 * Operator UX 2026-06-06 ("267件が全部ここに一覧に出てるわけじゃなくて
 * カードの中にカードがあって…数が合わない"): the board counts displayed at
 * the top never summed to the store's actual task count because the canvas
 * only shows the CURRENT drill scope while children of parent nodes hide
 * inside the "N ↓" pill, and the Pool sidebar carries the disconnected
 * tasks. This component makes the arithmetic explicit:
 *
 *   - Total      = every task in the store (graph.task_count).
 *   - Showing    = nodes rendered on the canvas RIGHT NOW (current drill
 *                  scope, partitioned, NOT including children of parents
 *                  that haven't been drilled into).
 *   - Nested     = how many tasks live INSIDE the parent cards currently
 *                  on the canvas (you'd see them by drilling into a parent).
 *   - Pool       = uncategorized / disconnected tasks in the sidebar at
 *                  the current scope.
 *
 * The four add up to the total at the top scope; deeper scopes sum to the
 * scope's total (the chip flips its tooltip to make that explicit). */
function CountBreakdown({ graph }: { graph: GraphPayload }) {
  const drillPath = useBoardStore((s) => s.drillPath);
  const scope = drillPath.length ? drillPath[drillPath.length - 1] : null;

  const counts = useMemo(() => {
    const total = graph.task_count;
    // Scope-aware partition: graphNodes = the connected dependency subgraph
    // for THIS drill level; poolNodes = uncategorized/disconnected at THIS
    // level.  See `partitionNodes` in layout.ts.
    const { graphNodes, poolNodes } = partitionNodes(graph, scope);
    const showing = graphNodes.length;
    const pool = poolNodes.length;
    // "Nested" = number of tasks hidden inside parent cards currently on
    // the canvas. Sum nodeChildCount for parent nodes only (kids > 0).
    let nested = 0;
    for (const n of graphNodes) nested += nodeChildCount(graph, n.id);
    return { total, showing, nested, pool };
  }, [graph, scope]);

  const tooltip = scope
    ? `Scope counts at this drill level (parent: ${scope}). "Total" is the full store.`
    : `Total store size + breakdown of what is on the canvas vs hidden in parent cards vs in the Pool.`;

  return (
    <span className="stx-todo-counts" aria-label="Task counts" title={tooltip}>
      <span className="stx-todo-counts__chip stx-todo-counts__chip--total">
        Total {counts.total}
      </span>
      <span className="stx-todo-counts__sep" aria-hidden="true">
        ·
      </span>
      <span
        className="stx-todo-counts__chip"
        title="Tasks rendered on the Canvas right now (this drill scope)"
      >
        Showing {counts.showing}
      </span>
      <span className="stx-todo-counts__sep" aria-hidden="true">
        ·
      </span>
      <span
        className="stx-todo-counts__chip"
        title="Tasks hidden inside parent cards on the canvas — drill in to see"
      >
        Nested {counts.nested}
      </span>
      <span className="stx-todo-counts__sep" aria-hidden="true">
        ·
      </span>
      <span
        className="stx-todo-counts__chip"
        title="Uncategorized / disconnected tasks in the Pool sidebar"
      >
        Pool {counts.pool}
      </span>
    </span>
  );
}

export function TodoBoard() {
  const { graph, loading, error, load } = useBoardStore();
  const view = useBoardStore((s) => s.view);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !graph) {
    return <div className="stx-todo-status">Loading task graph…</div>;
  }
  if (error) {
    return (
      <div className="stx-todo-status stx-todo-status--err">Error: {error}</div>
    );
  }
  if (!graph) {
    return <div className="stx-todo-status">No graph.</div>;
  }

  return (
    <div className="stx-todo-board">
      <header className="stx-todo-board__header">
        {/* "Board" region hint — operator UX 2026-06-06: "canvas/drill/pool/
         * table/board とか UI 上にヒント的に書いておいて" — pairs with the
         * "Drill:" label on the breadcrumb, "Canvas" on the React Flow root,
         * and the "Pool —" prefix in the UncategorizedPool. The original
         * "SciTeX Todo — dependency graph" still sits next to it as the
         * full title; the new chip is just the at-a-glance region name. */}
        <span
          className="stx-todo-board__region"
          aria-hidden="true"
          title="Board — the whole dependency graph page"
        >
          Board
        </span>
        <span className="stx-todo-board__title">
          SciTeX Todo — dependency graph
        </span>
        <span className="stx-todo-board__meta">
          <code>{graph.store_path}</code>
        </span>
        <CountBreakdown graph={graph} />
        <Progress graph={graph} />
        {/* Fleet CI-status pills (Phase 1 of FLEET DASHBOARD vision —
         * approved via a2a `74db4f2d`). Polls /fleet/ci-status every
         * 30s; gracefully hides itself when no repos are configured
         * (showing a small "no CI status configured" footnote). The
         * registry-reader harness in handlers/fleet/ is the template
         * subsequent waves (hosts / mesh / timing / chat) plug into. */}
        <FleetCiPills />
        {/* Fleet timing-chart panel (Phase 5 of FLEET DASHBOARD — lead
         * a2a `74db4f2d` + `10afa799`, operator ask 2026-06-14:
         * "record what took how long → self-improvement"). Polls
         * `/fleet/timing?window_days=<state>` every 60s (timing
         * changes slowly). Default collapsed pill in the STATUS group;
         * click to expand into the bar chart sorted by p95 desc
         * (bottleneck-at-top). Same registry-reader pattern as the
         * mesh / hosts panels. */}
        <FleetTimingPanel />
        <ViewToggle />
        <Legend colors={graph.status_colors} />
      </header>
      <Toolbar graph={graph} />
      <div className="stx-todo-board__canvas">
        {view === "graph" ? (
          <GraphView graph={graph} />
        ) : view === "recent" ? (
          <RecentView graph={graph} />
        ) : view === "calendar" ? (
          <CalendarView graph={graph} />
        ) : view === "timeline" ? (
          <TimelineView statusColors={graph.status_colors} />
        ) : (
          <TableView graph={graph} />
        )}
        {/* Drawer, context menu, and live-refresh poller are shared by both
            views, so they live here rather than inside GraphView. */}
        <NodeDetailPanelContainer />
        <ContextMenu />
        <EdgeKindMenu />
        <AutoRefresh />
        <DrillHistory />
        <Toast />
        <KeyboardShortcuts />
      </div>
    </div>
  );
}
