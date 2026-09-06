/** Flat, sortable table view of the task store — a fast triage surface for a
 * large store (the graph is great for structure, less so for scanning 200
 * rows). Honors the toolbar search + status filter. Rows open the same detail
 * drawer on click and the same right-click context menu as the graph; a row
 * with children drills into its subgraph instead.
 *
 * Unlike the graph (which is scoped by the drill path), the table shows ALL
 * matching tasks flat — that's the point of the view.
 */

import { useMemo, useState } from "react";
import { nodeHasChildren } from "./layout";
import { taskMatchesFilter, useBoardStore } from "./store/useBoardStore";
import { isVisibleRow } from "./tableFilter";
import type { GraphNode, GraphPayload } from "./types/board";

type SortKey = "title" | "status" | "priority" | "repo" | "deps" | "comments";
type SortDir = "asc" | "desc";

interface Row {
  node: GraphNode;
  deps: number;
  comments: number;
  hasChildren: boolean;
}

const COLUMNS: { key: SortKey; label: string; className?: string }[] = [
  { key: "title", label: "Title" },
  { key: "status", label: "Status" },
  { key: "priority", label: "Prio" },
  { key: "repo", label: "Repo" },
  { key: "deps", label: "Deps" },
  { key: "comments", label: "💬" },
];

export function TableView({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillInto = useBoardStore((s) => s.drillInto);
  const openMenu = useBoardStore((s) => s.openMenu);
  const selectedIds = useBoardStore((s) => s.selectedIds);
  const toggleSelected = useBoardStore((s) => s.toggleSelected);
  const clearSelected = useBoardStore((s) => s.clearSelected);
  const [sortKey, setSortKey] = useState<SortKey>("priority");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  // Hide structural umbrella cards (kind=status / kind=goal) by default —
  // operator pain via lead a2a 510a58d4: the flat Table view is cluttered
  // by q-* quality-axis rows and goal/umbrella anchors that are not
  // actionable tasks. The Graph + Column views are UNTOUCHED — they keep
  // showing every card per the existing dep-graph contract. See
  // ./tableFilter.ts for the full rationale.
  const [showStructural, setShowStructural] = useState<boolean>(false);

  // Count edges touching each node once, so the Deps column is O(E) not O(N·E).
  const degree = useMemo(() => {
    const d = new Map<string, number>();
    for (const e of graph.edges) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1);
      d.set(e.target, (d.get(e.target) ?? 0) + 1);
    }
    return d;
  }, [graph.edges]);

  const rows = useMemo<Row[]>(() => {
    const filtered = graph.nodes.filter(
      (n) =>
        taskMatchesFilter(n, query, activeStatuses, activeRepos) &&
        isVisibleRow(n, showStructural),
    );
    const mapped: Row[] = filtered.map((n) => ({
      node: n,
      deps: degree.get(n.id) ?? 0,
      comments: n.comments?.length ?? 0,
      hasChildren: nodeHasChildren(graph, n.id),
    }));
    const dir = sortDir === "asc" ? 1 : -1;
    const val = (r: Row): string | number => {
      switch (sortKey) {
        case "title":
          return r.node.title.toLowerCase();
        case "status":
          return r.node.status;
        case "priority":
          // Nulls sort last regardless of direction.
          return r.node.priority ?? Number.POSITIVE_INFINITY;
        case "repo":
          return (r.node.repo ?? "").toLowerCase();
        case "deps":
          return r.deps;
        case "comments":
          return r.comments;
      }
    };
    return mapped.sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return a.node.title.localeCompare(b.node.title);
    });
  }, [
    graph,
    degree,
    query,
    activeStatuses,
    activeRepos,
    sortKey,
    sortDir,
    showStructural,
  ]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className="stx-cards-table-wrap">
      <div className="stx-cards-table__toolbar">
        <label
          className="stx-cards-table__toggle"
          title={
            "Show structural cards (kind=status, kind=goal) — quality-axis " +
            "rows and goal/umbrella anchors. Hidden by default; they still " +
            "show in the Graph + Column views."
          }
        >
          <input
            type="checkbox"
            checked={showStructural}
            onChange={(e) => setShowStructural(e.target.checked)}
          />
          <span>Show structural cards</span>
        </label>
      </div>
      <table className="stx-cards-table">
        <thead>
          <tr>
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                className={`stx-cards-table__th${
                  sortKey === col.key ? " stx-cards-table__th--sorted" : ""
                }`}
                onClick={() => onSort(col.key)}
                title={`Sort by ${col.label}`}
              >
                {col.label}
                {sortKey === col.key && (
                  <span className="stx-cards-table__caret" aria-hidden="true">
                    {sortDir === "asc" ? " ▲" : " ▼"}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node, deps, comments, hasChildren }) => {
            const c = graph.status_colors[node.status];
            const selected = selectedIds.includes(node.id);
            return (
              <tr
                key={node.id}
                className={`stx-cards-table__row${
                  selected ? " stx-cards-table__row--selected" : ""
                }`}
                onClick={(e) => {
                  if (e.ctrlKey || e.metaKey) {
                    toggleSelected(node.id);
                    return;
                  }
                  clearSelected();
                  if (hasChildren) drillInto(node.id);
                  else selectNode(node.id);
                }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  openMenu(e.clientX, e.clientY, node.id);
                }}
                title={
                  hasChildren
                    ? "Drill in (right-click to edit)"
                    : "Details (right-click to edit)"
                }
              >
                <td className="stx-cards-table__title">
                  {hasChildren ? "▸ " : ""}
                  {node.title}
                </td>
                <td>
                  <span
                    className="stx-cards-table__status"
                    style={{
                      background: c?.fill ?? "#888",
                      borderColor: c?.stroke ?? "#888",
                    }}
                  >
                    {node.status}
                  </span>
                </td>
                <td className="stx-cards-table__num">{node.priority ?? ""}</td>
                <td className="stx-cards-table__repo">{node.repo ?? ""}</td>
                <td className="stx-cards-table__num">{deps || ""}</td>
                <td className="stx-cards-table__num">{comments || ""}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td className="stx-cards-table__empty" colSpan={COLUMNS.length}>
                No matching tasks.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
