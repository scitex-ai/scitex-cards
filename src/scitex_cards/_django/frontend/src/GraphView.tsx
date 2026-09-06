/** React Flow rendering of the task dependency graph.
 *
 * Interaction model:
 *   - Graph nodes are DRAGGABLE. On drag-end, all current node positions
 *     are sorted top→bottom (then left→right within a y-band) to derive a
 *     priority order, which is POSTed to `/priority` via the board store
 *     (`reorderPriority`). The backend writes the YAML via `save_tasks`,
 *     and the store reloads the graph from the canonical source of truth.
 *   - The Uncategorized pool is a DOCKED, collapsible left sidebar (not a
 *     floating overlay) so it never covers the canvas.
 *   - The toolbar filter (search + status chips) DIMS non-matching graph
 *     nodes and HIDES non-matching pool items.
 *   - Click routing depends on whether the clicked node HAS CHILDREN:
 *       * has-children → DRILL IN; leaf → open the markdown detail drawer.
 *   - `fitView` re-runs whenever the drill scope or filter changes, so the
 *     visible nodes are always centered (no dead band).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  Background,
  Controls,
  MiniMap,
  PanOnScrollMode,
  ReactFlow,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  buildFlow,
  nodeChildCount,
  nodeHasChildren,
  nodeStyle,
  parentNodeStyle,
  partitionNodes,
} from "./layout";
import { InhibitionEdge, INHIBITION_EDGE_TYPE } from "./InhibitionEdge";
import {
  STATUSES,
  taskMatchesFilter,
  useBoardStore,
} from "./store/useBoardStore";
import { useTheme } from "./useTheme";
import type { GraphNode, GraphPayload } from "./types/board";

/* Canvas chrome (Background dots + MiniMap) per theme. React Flow's own
 * `colorMode` handles node/edge/control base theming; these are the bits it
 * doesn't infer, so we flip them alongside the shell's data-theme. */
const FLOW_CHROME = {
  dark: {
    miniMapBg: "#1b1b29",
    miniMapMask: "rgba(20, 20, 32, 0.78)",
    miniMapNode: "#3a3a52",
    miniMapNodeStroke: "#9b7fd6",
    bgDots: "#33334a",
  },
  light: {
    miniMapBg: "#f3f2f0",
    miniMapMask: "rgba(225, 223, 219, 0.78)",
    miniMapNode: "#c9c4d8",
    miniMapNodeStroke: "#6a4fb0",
    bgDots: "#c8c6c2",
  },
} as const;

const EDGE_TYPES: EdgeTypes = {
  [INHIBITION_EDGE_TYPE]: InhibitionEdge,
};

const Y_BAND_PX = 24;

/** Sort node ids by (y, x) screen position into a top-priority-first list. */
export function nodesToPriorityOrder(nodes: Node[]): string[] {
  return [...nodes]
    .sort((a, b) => {
      const dy = a.position.y - b.position.y;
      if (Math.abs(dy) > Y_BAND_PX) return dy;
      return a.position.x - b.position.x;
    })
    .map((n) => n.id);
}

/** Re-fit the viewport whenever `dep` changes (drill scope / filter / reload),
 * so the currently-visible nodes are always centered with no dead band.
 * When `focusIds` is non-empty (an active search/filter), zoom to JUST those
 * matching nodes — a "search-jump" — instead of fitting the whole scope.
 * Lives inside <ReactFlow> so `useReactFlow()` has a provider. */
function FitOnChange({ dep, focusIds }: { dep: string; focusIds: string[] }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    // rAF: let React Flow apply the new nodes before measuring.
    const id = requestAnimationFrame(() => {
      if (focusIds.length > 0) {
        fitView({
          padding: 0.3,
          duration: 300,
          maxZoom: 1.5,
          nodes: focusIds.map((nid) => ({ id: nid })),
        });
      } else {
        fitView({ padding: 0.2, duration: 200 });
      }
    });
    return () => cancelAnimationFrame(id);
    // focusIds is folded into `dep` by the caller, so depend on dep only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep, fitView]);
  return null;
}

/** Breadcrumb bar above the canvas. */
function Breadcrumb({
  graph,
  drillPath,
  drillTo,
}: {
  graph: GraphPayload;
  drillPath: string[];
  drillTo: (depth: number) => void;
}) {
  const titles = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n.title]));
    return drillPath.map((id) => byId.get(id) ?? id);
  }, [graph.nodes, drillPath]);

  // Always rendered (even at the top level, where it shows just "Home") so it
  // occupies a fixed strip and the layout never jumps as you drill in/out.
  const atTop = drillPath.length === 0;

  // Explicit "← Back" button when drilled in — operator UX 2026-06-06 msg
  // 233 ("クリックしたあとえ、なに？どゆこと？"): after a drill-click the
  // breadcrumb's "Home" crumb IS clickable but the operator didn't read it
  // as a back-button. Give them an unambiguous arrow they can hit instead
  // (one level up at a time, parallels the browser back button affordance).
  // Hidden at top level (atTop) — there's nowhere to go.
  const onBack = () => drillTo(drillPath.length - 1);
  return (
    <nav className="stx-cards-breadcrumb" aria-label="Drill-down breadcrumb">
      {/* "Drill:" prefix label (operator UX 2026-06-06: "canvas/drill/pool/
        * table/board とか UI 上にヒント的に書いておいて") so the user can see
        * THIS strip = the drill-down state, not a generic title bar. The
        * label is visually muted so it doesn't fight the breadcrumb crumbs.
        */}
      <span
        className="stx-cards-breadcrumb__hint"
        aria-hidden="true"
        title="Drill-down path — click a parent crumb to go back"
      >
        Drill:
      </span>
      {!atTop && (
        <button
          type="button"
          className="stx-cards-breadcrumb__back"
          onClick={onBack}
          title="Go up one level (or click any crumb below)"
          aria-label="Back one drill level"
        >
          ← Back
        </button>
      )}
      {atTop ? (
        <span
          className="stx-cards-breadcrumb__crumb stx-cards-breadcrumb__crumb--current"
          aria-current="page"
        >
          Home
        </span>
      ) : (
        <button
          type="button"
          className="stx-cards-breadcrumb__crumb"
          onClick={() => drillTo(0)}
        >
          Home
        </button>
      )}
      {titles.map((title, idx) => {
        const isCurrent = idx === titles.length - 1;
        const separator = (
          <span className="stx-cards-breadcrumb__sep" aria-hidden="true">
            /
          </span>
        );
        if (isCurrent) {
          return (
            <span key={`${drillPath[idx]}-${idx}`}>
              {separator}
              <span
                className="stx-cards-breadcrumb__crumb stx-cards-breadcrumb__crumb--current"
                aria-current="page"
              >
                {title}
              </span>
            </span>
          );
        }
        return (
          <span key={`${drillPath[idx]}-${idx}`}>
            {separator}
            <button
              type="button"
              className="stx-cards-breadcrumb__crumb"
              onClick={() => drillTo(idx + 1)}
            >
              {title}
            </button>
          </span>
        );
      })}
    </nav>
  );
}

/** Docked, collapsible sidebar of tasks not connected into the dependency
 * graph (note == "uncategorized" or no sibling edges at this scope). Honors
 * the toolbar filter: non-matching items are hidden. */
function UncategorizedPool({
  graph,
  scope,
  query,
  activeStatuses,
  activeRepos,
}: {
  graph: GraphPayload;
  scope: string | null;
  query: string;
  activeStatuses: string[];
  activeRepos: string[];
}) {
  const poolNodes = useMemo(
    () => partitionNodes(graph, scope).poolNodes,
    [graph, scope],
  );
  const visible = useMemo(
    () =>
      poolNodes.filter((n) =>
        taskMatchesFilter(n, query, activeStatuses, activeRepos),
      ),
    [poolNodes, query, activeStatuses, activeRepos],
  );
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillInto = useBoardStore((s) => s.drillInto);
  const openMenu = useBoardStore((s) => s.openMenu);
  const beginCreate = useBoardStore((s) => s.beginCreate);
  const selectedIds = useBoardStore((s) => s.selectedIds);
  const toggleSelected = useBoardStore((s) => s.toggleSelected);
  const clearSelected = useBoardStore((s) => s.clearSelected);
  // Expanded by default; collapse to the thin rail via the title toggle.
  const [open, setOpen] = useState(true);

  // Group the visible pool items by status, in canonical STATUSES order, so
  // the flat inbox reads as collapsible structural sections instead of one
  // long undifferentiated list.
  const groups = useMemo(() => {
    const byStatus = new Map<string, GraphNode[]>();
    for (const n of visible) {
      const arr = byStatus.get(n.status) ?? [];
      arr.push(n);
      byStatus.set(n.status, arr);
    }
    const ordered: { status: string; items: GraphNode[] }[] = [];
    for (const s of STATUSES) {
      const items = byStatus.get(s);
      if (items && items.length) ordered.push({ status: s, items });
    }
    // Any status not in the canonical list (defensive) trails alphabetically.
    for (const [s, items] of [...byStatus.entries()].sort()) {
      if (!STATUSES.includes(s as (typeof STATUSES)[number])) {
        ordered.push({ status: s, items });
      }
    }
    return ordered;
  }, [visible]);

  if (poolNodes.length === 0) return null;

  const renderItem = (n: GraphNode) => {
    const prio = n.priority != null ? ` · p${n.priority}` : "";
    const kids = nodeChildCount(graph, n.id);
    const hasChildren = kids > 0;
    const baseStyle = nodeStyle(graph.status_colors[n.status]);
    const style = hasChildren
      ? parentNodeStyle(baseStyle, kids, graph.status_colors[n.status])
      : baseStyle;
    const selected = selectedIds.includes(n.id);
    const onClick = (e: ReactMouseEvent) => {
      if (e.ctrlKey || e.metaKey) {
        toggleSelected(n.id);
        return;
      }
      clearSelected();
      if (hasChildren) drillInto(n.id);
      else selectNode(n.id);
    };
    return (
      <button
        type="button"
        key={n.id}
        className={`${
          hasChildren
            ? "stx-cards-pool__item stx-cards-pool__item--parent"
            : "stx-cards-pool__item stx-cards-pool__item--leaf"
        }${selected ? " stx-cards-pool__item--selected" : ""}`}
        style={style}
        onClick={onClick}
        onContextMenu={(e) => {
          e.preventDefault();
          openMenu(e.clientX, e.clientY, n.id);
        }}
        title={
          hasChildren
            ? "Drill in (right-click to edit)"
            : "Details (right-click to edit)"
        }
        aria-label={
          hasChildren
            ? `Drill into ${n.title} (${kids} ${
                kids === 1 ? "child" : "children"
              })`
            : `Open details for ${n.title}`
        }
      >
        {hasChildren && (
          <span className="stx-cards-pool__badge" aria-hidden="true">
            {kids} ↓
          </span>
        )}
        {hasChildren ? (
          <>
            <span className="stx-cards-node__glyph" aria-hidden="true">
              ⊞{" "}
            </span>
            {n.title}
          </>
        ) : (
          n.title
        )}
        {n.repo ? ` · ${n.repo}` : ""}
        {prio}
        {n.comments?.length ? `  💬${n.comments.length}` : ""}
      </button>
    );
  };

  // The sidebar label adapts to the drill scope. At top level it's the
  // standard "Pool — Uncategorized (N)" (orphan tasks with no dep edges).
  // When drilled into a parent, the pool actually carries the CHILDREN of
  // that parent that don't connect to siblings inside the scope — the
  // operator's confusion (2026-06-06 photo 8 + msg 233): "クリックした
  // あとえ、なに？どゆこと？" — they saw an EMPTY canvas + "UNCATEGORIZED"
  // sidebar and didn't realize the tasks they expected ARE in the sidebar,
  // they're just labelled with the wrong umbrella. So when scope != null,
  // we say "Children of <parent title>" instead, which truthfully names
  // what's there.
  const parentTitle = scope
    ? (graph.nodes.find((n) => n.id === scope)?.title ?? scope)
    : null;
  const label = parentTitle
    ? `Children of ${parentTitle} (${visible.length})`
    : `Pool — Uncategorized (${visible.length})`;
  const ariaLabel = parentTitle
    ? `Children of ${parentTitle} (${visible.length} tasks)`
    : `Uncategorized tasks (${visible.length})`;

  // Collapsed: a thin rail with just an expand affordance, so the canvas
  // reclaims the full width instead of reserving an empty sidebar column.
  if (!open) {
    return (
      <aside
        className="stx-cards-pool stx-cards-pool--collapsed"
        aria-label={`${ariaLabel} (collapsed)`}
      >
        <button
          type="button"
          className="stx-cards-pool__expand"
          onClick={() => setOpen(true)}
          aria-expanded={false}
          title={`Show ${label}`}
          aria-label={`Show ${ariaLabel}`}
        >
          <span className="stx-cards-pool__expand-glyph" aria-hidden="true">
            ▸
          </span>
          <span className="stx-cards-pool__expand-label">{label}</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="stx-cards-pool" aria-label={ariaLabel}>
      <div className="stx-cards-pool__head">
        <button
          type="button"
          className="stx-cards-pool__title"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title="Collapse"
        >
          ▾ {label}
        </button>
        <button
          type="button"
          className="stx-cards-pool__new"
          onClick={beginCreate}
          title="New task"
          aria-label="New task"
        >
          ✚
        </button>
      </div>
      {open && (
        <div className="stx-cards-pool__groups">
          {groups.map(({ status, items }) => (
            <details className="stx-cards-pool__group" key={status}>
              <summary className="stx-cards-pool__group-summary">
                <span
                  className="stx-cards-pool__group-swatch"
                  style={{
                    background: graph.status_colors[status]?.fill ?? "#888",
                    borderColor: graph.status_colors[status]?.stroke ?? "#888",
                  }}
                  aria-hidden="true"
                />
                {status} ({items.length})
              </summary>
              <div className="stx-cards-pool__items">
                {items.map(renderItem)}
              </div>
            </details>
          ))}
          {visible.length === 0 && (
            <span className="stx-cards-pool__empty">no matches</span>
          )}
        </div>
      )}
    </aside>
  );
}

export function GraphView({ graph }: { graph: GraphPayload }) {
  const reorderPriority = useBoardStore((s) => s.reorderPriority);
  const saving = useBoardStore((s) => s.saving);
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillPath = useBoardStore((s) => s.drillPath);
  const drillInto = useBoardStore((s) => s.drillInto);
  const drillTo = useBoardStore((s) => s.drillTo);
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const openMenu = useBoardStore((s) => s.openMenu);
  const setEdge = useBoardStore((s) => s.setEdge);
  const openEdgePicker = useBoardStore((s) => s.openEdgePicker);
  const updateTask = useBoardStore((s) => s.updateTask);
  const selectedIds = useBoardStore((s) => s.selectedIds);
  const toggleSelected = useBoardStore((s) => s.toggleSelected);
  const clearSelected = useBoardStore((s) => s.clearSelected);
  const showToast = useBoardStore((s) => s.showToast);

  // Last pointer position, so onConnect (which has no mouse coords) can place
  // the edge-kind picker where the connection was dropped.
  const pointer = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      pointer.current = { x: e.clientX, y: e.clientY };
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  const theme = useTheme();
  const chrome = FLOW_CHROME[theme];

  const scope = drillPath.length > 0 ? drillPath[drillPath.length - 1] : null;

  const seeded = useMemo<{ nodes: Node[]; edges: Edge[] }>(
    () => buildFlow(graph, scope),
    [graph, scope],
  );
  const [nodes, setNodes] = useState<Node[]>(seeded.nodes);
  const [edges, setEdges] = useState<Edge[]>(seeded.edges);

  useEffect(() => {
    setNodes(seeded.nodes);
    setEdges(seeded.edges);
  }, [seeded]);

  // Lookup task metadata by id so we can test the filter against the
  // draggable node state (whose `data` only carries the rendered label).
  const byId = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of graph.nodes) m.set(n.id, n);
    return m;
  }, [graph.nodes]);

  const filtering =
    query.trim().length > 0 ||
    activeStatuses.length > 0 ||
    activeRepos.length > 0;

  // Dim non-matching graph nodes (keep them in place so the structure reads),
  // and ring any Ctrl+click multi-selected card.
  const viewNodes = useMemo<Node[]>(() => {
    if (!filtering && selectedIds.length === 0) return nodes;
    const sel = new Set(selectedIds);
    return nodes.map((n) => {
      const task = byId.get(n.id);
      const match =
        !filtering ||
        (task
          ? taskMatchesFilter(task, query, activeStatuses, activeRepos)
          : true);
      const selected = sel.has(n.id);
      return {
        ...n,
        style: {
          ...n.style,
          opacity: match ? 1 : 0.16,
          outline: selected ? "3px solid var(--stx-accent)" : undefined,
          outlineOffset: selected ? "2px" : undefined,
        },
      };
    });
  }, [nodes, byId, filtering, query, activeStatuses, activeRepos, selectedIds]);

  // Ids of the in-scope nodes that match the active filter — the search-jump
  // target so the viewport zooms to the matches instead of the whole scope.
  const focusIds = useMemo<string[]>(() => {
    if (!filtering) return [];
    return nodes
      .filter((n) => {
        const task = byId.get(n.id);
        return task
          ? taskMatchesFilter(task, query, activeStatuses, activeRepos)
          : false;
      })
      .map((n) => n.id);
  }, [nodes, byId, filtering, query, activeStatuses, activeRepos]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  // Drag-to-connect / drag-to-group, by PROXIMITY to the nearest other card:
  //   heavy overlap (≥50%)         → group: dragged card nests under target
  //   close (gap within threshold) → offer an edge (arrow/blocker picker),
  //                                   dragged = source, nearby card = target
  //   neither                      → reposition (persist the priority order)
  // Proximity (not just overlap) is the point — you don't have to land on the
  // circle handle, just drag a card near another.
  const GROUP_OVERLAP = 0.5;
  const PROXIMITY_PX = 60;
  const onNodeDragStop = useCallback(
    (event: ReactMouseEvent, node: Node) => {
      setNodes((current) => {
        const rect = (n: Node) => {
          const m = (n as { measured?: { width?: number; height?: number } })
            .measured;
          return {
            x: n.position.x,
            y: n.position.y,
            w: m?.width ?? 200,
            h: m?.height ?? 60,
          };
        };
        const dr = rect(node);
        const dArea = Math.max(1, dr.w * dr.h);
        let near: Node | null = null;
        let nearGap = Infinity;
        let nearRatio = 0;
        for (const o of current) {
          if (o.id === node.id) continue;
          const r = rect(o);
          const ox = Math.max(
            0,
            Math.min(dr.x + dr.w, r.x + r.w) - Math.max(dr.x, r.x),
          );
          const oy = Math.max(
            0,
            Math.min(dr.y + dr.h, r.y + r.h) - Math.max(dr.y, r.y),
          );
          // Edge-to-edge gap (0 when the boxes overlap on that axis).
          const gx = Math.max(0, r.x - (dr.x + dr.w), dr.x - (r.x + r.w));
          const gy = Math.max(0, r.y - (dr.y + dr.h), dr.y - (r.y + r.h));
          const gap = Math.hypot(gx, gy);
          if (gap < nearGap) {
            nearGap = gap;
            nearRatio = (ox * oy) / dArea;
            near = o;
          }
        }
        if (near && nearRatio >= GROUP_OVERLAP) {
          const prevParent = byId.get(node.id)?.parent ?? null;
          const childId = node.id;
          void updateTask(childId, { parent: near.id });
          showToast("Grouped card", () => {
            void updateTask(childId, { parent: prevParent });
          });
        } else if (near && nearGap <= PROXIMITY_PX) {
          openEdgePicker(node.id, near.id, event.clientX, event.clientY);
        } else {
          void reorderPriority(nodesToPriorityOrder(current));
        }
        return current;
      });
    },
    [reorderPriority, updateTask, openEdgePicker, byId, showToast],
  );

  const onNodeClick = useCallback(
    (event: ReactMouseEvent, node: Node) => {
      // Ctrl/⌘+click toggles multi-selection (for bulk copy) instead of
      // opening the drawer / drilling in.
      if (event.ctrlKey || event.metaKey) {
        toggleSelected(node.id);
        return;
      }
      clearSelected();
      if (nodeHasChildren(graph, node.id)) {
        drillInto(node.id);
      } else {
        selectNode(node.id);
      }
    },
    [graph, drillInto, selectNode, toggleSelected, clearSelected],
  );

  // Right-click a node -> card context menu (edit / status / delete).
  const onNodeContextMenu = useCallback(
    (event: ReactMouseEvent, node: Node) => {
      event.preventDefault();
      openMenu(event.clientX, event.clientY, node.id);
    },
    [openMenu],
  );

  // Right-click empty canvas -> pane context menu (new task).
  const onPaneContextMenu = useCallback(
    (event: MouseEvent | ReactMouseEvent) => {
      event.preventDefault();
      openMenu(
        (event as ReactMouseEvent).clientX,
        (event as ReactMouseEvent).clientY,
        null,
      );
    },
    [openMenu],
  );

  // Drag from one node's handle to another -> offer a kind choice (arrow =
  // depends_on, blocker = blocks) at the drop point, rather than assuming one.
  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source && c.target && c.source !== c.target) {
        const p = pointer.current;
        openEdgePicker(c.source, c.target, p.x, p.y);
      }
    },
    [openEdgePicker],
  );

  // Right-click an edge -> confirm + delete it (kind carried in edge.data).
  const onEdgeContextMenu = useCallback(
    (event: ReactMouseEvent, edge: Edge) => {
      event.preventDefault();
      const kind =
        (edge.data?.kind as "depends_on" | "blocks" | undefined) ??
        "depends_on";
      const label = kind === "blocks" ? "blocks" : "depends-on";
      if (window.confirm(`Delete this ${label} edge?`)) {
        void setEdge("remove", kind, edge.source, edge.target);
      }
    },
    [setEdge],
  );

  // Re-fit key: scope + filter + node count drive a viewport re-fit.
  const fitKey = `${scope ?? "_top"}|${query}|${activeStatuses
    .slice()
    .sort()
    .join(",")}|${seeded.nodes.length}`;

  return (
    <div className={`stx-cards-flow${saving ? " stx-cards-flow--saving" : ""}`}>
      <Breadcrumb graph={graph} drillPath={drillPath} drillTo={drillTo} />
      <div className="stx-cards-flow__body">
        <UncategorizedPool
          graph={graph}
          scope={scope}
          query={query}
          activeStatuses={activeStatuses}
          activeRepos={activeRepos}
        />
        <div className="stx-cards-flow__canvas">
          {/* Region hint label (operator UX 2026-06-06): floating top-left
            * pill saying "Canvas" so the user can name what they're looking
            * at vs the Pool aside and the Breadcrumb strip. Pointer-events
            * disabled so it never steals clicks from the React Flow surface
            * underneath. */}
          <span
            className="stx-cards-flow__canvas-label"
            aria-hidden="true"
            title="Canvas — the connected dependency graph for the current drill scope"
          >
            Canvas
          </span>
          {/* Empty-canvas explainer (operator UX 2026-06-06 photo 8 + msg
            * 233 "クリックしたあとえ、なに？どゆこと？"): when the operator
            * drills into a parent and the canvas comes up blank, it reads
            * as "nothing happened / nothing here", because the only
            * children live as un-connected tasks in the Pool sidebar (no
            * sibling-edges inside the scope). Surface an explicit message
            * that names exactly what they're seeing + how to leave.
            * `pointer-events:none` so the underlying React Flow background
            * still receives pan/zoom interactions through this overlay. */}
          {viewNodes.length === 0 && (
            <div
              className="stx-cards-flow__empty"
              role="status"
              aria-live="polite"
            >
              <p className="stx-cards-flow__empty-title">
                {scope
                  ? "No dependency edges inside this scope."
                  : "No connected tasks to render."}
              </p>
              <p className="stx-cards-flow__empty-body">
                {scope
                  ? "The children of this parent live in the sidebar on the left (they don't depend on each other inside this scope yet). Click any sidebar card to open its details, or click ⌀ Home in the breadcrumb above to go back."
                  : "All tasks are uncategorized — see the Pool sidebar on the left."}
              </p>
            </div>
          )}
          <ReactFlow
            nodes={viewNodes}
            edges={edges}
            edgeTypes={EDGE_TYPES}
            onNodesChange={onNodesChange}
            onNodeDragStop={onNodeDragStop}
            onNodeClick={onNodeClick}
            onNodeContextMenu={onNodeContextMenu}
            onPaneContextMenu={onPaneContextMenu}
            onConnect={onConnect}
            onEdgeContextMenu={onEdgeContextMenu}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            colorMode={theme}
            nodesDraggable={true}
            nodesConnectable={true}
            elementsSelectable={true}
            // Operator UX 2026-06-06 Telegram 227: "ドラッグスクロールか
            // スクロールを上下左右にすると左右がまず動かなくて上下がズーム
            // アウトになってしまってる. ズームイン OUT は Control を押し
            // ながらのスクロールでお願いします." Switch to Google-Maps /
            // Miro / Figma-style scroll behaviour:
            //   - plain wheel / trackpad two-finger scroll → PAN (free, in
            //     any direction — panOnScroll:true + panOnScrollMode:"free")
            //   - Ctrl + wheel → ZOOM (zoomActivationKeyCode:"Control" gates
            //     the existing zoomOnScroll behaviour so plain scroll no
            //     longer changes zoom).
            // Pinch-to-zoom on trackpads keeps working via zoomOnPinch
            // (default true, left implicit).
            panOnScroll={true}
            panOnScrollMode={PanOnScrollMode.Free}
            zoomActivationKeyCode="Control"
            proOptions={{ hideAttribution: true }}
          >
            <FitOnChange dep={fitKey} focusIds={focusIds} />
            <Background color={chrome.bgDots} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              maskColor={chrome.miniMapMask}
              nodeColor={chrome.miniMapNode}
              nodeStrokeColor={chrome.miniMapNodeStroke}
              style={{ background: chrome.miniMapBg }}
            />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}
