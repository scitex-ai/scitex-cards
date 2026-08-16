/** RecentView — newest-first triage surface (operator TG msg 513,
 * 2026-06-12). "Make a Recent / 最近のToDo UI. There are many ToDos now —
 * I want to see at a glance when something new comes in."
 *
 * Design framing (dogfooding loop, lead-aligned): this view is the
 * fleet's FEEDBACK INTAKE surface, not just sort-by-date. When a paper
 * or a project drops 3 new tasks in a morning, the operator scans the
 * Recent view, eyeballs the project chips, and asks "what's the
 * abstractable pattern?". So each row is a card carrying:
 *
 *  - Relative timestamp ("2h ago") + absolute ISO on hover
 *  - Status pill (existing palette)
 *  - PROJECT chip (color-stable by project name) — entry point for the
 *    cross-project triage signal
 *  - Title (click → detail drawer)
 *  - Assignee chip + Priority pill
 *
 * NEW badge (recency tint):
 *  - last 24h → bright orange "NEW 🆕"
 *  - 24-72h   → subtle yellow left-border tint
 *  - older    → plain row
 *
 * Filter strip: REUSES the toolbar's qualifier-syntax search (PR #102)
 * via the shared `taskMatchesFilter` helper — so typing
 * `project:paper-scitex-clew status:pending` filters the Recent view.
 *
 * Default lookback: 30 days. A "Show older" toggle reveals the rest.
 */

import { useMemo, useState } from "react";
import {
  classifyRecency,
  countNewIn24h,
  filterDefaultLookback,
  relativeTimestamp,
  sortByRecency,
  taskTimestamp,
  type RecencyClass,
} from "./recentSort";
import { taskMatchesFilter, useBoardStore } from "./store/useBoardStore";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";

/** Stable hash-of-string → 0..359 hue for the project chip background.
 *
 * Cheap deterministic hash so each project chip gets the same hue every
 * render. The operator's scanning task ("which project added 3 things
 * today") is the load-bearing UX — color stability matters more than
 * palette aesthetics; we keep saturation/lightness fixed and only vary
 * hue.
 */
function projectHue(project: string | null | undefined): number {
  const s = project ?? "";
  if (!s) return 0;
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}

/** Parse a P0/P1/P2/P3 title prefix into an explicit numeric. Falls back
 * to the schema `priority` field when no prefix is present. Mirrors the
 * title-prefix convention documented in HANDOFF.md "Operating policy". */
function priorityLabel(
  node: GraphNode,
): { label: string; rank: number | null } | null {
  const m = /^\s*\[(P\d)\]/i.exec(node.title);
  if (m) {
    const tag = m[1].toUpperCase();
    return { label: tag, rank: Number(tag.slice(1)) };
  }
  if (typeof node.priority === "number") {
    return { label: `P${node.priority}`, rank: node.priority };
  }
  return null;
}

/** Render one Recent-view row. Click opens the detail drawer; right-
 * click opens the existing context menu (same as TableView). */
function RecentRow({
  node,
  statusColors,
  now,
}: {
  node: GraphNode;
  statusColors: Record<string, StatusColor>;
  now: Date;
}) {
  const selectNode = useBoardStore((s) => s.selectNode);
  const openMenu = useBoardStore((s) => s.openMenu);
  const recency: RecencyClass = classifyRecency(node as never, now);
  const { ts, source } = taskTimestamp(node as never);
  const rel = relativeTimestamp(ts, now);
  const iso = ts ? ts.toISOString() : "";
  const hoverTip =
    source === "comment"
      ? `${iso} (from first comment — task has no created_at)`
      : iso;
  const status = statusColors[node.status];
  const project =
    (node as GraphNode & { project?: string | null }).project ??
    node.repo ??
    null;
  const agent =
    (node as GraphNode & { agent?: string | null; assignee?: string | null })
      .agent ??
    (node as GraphNode & { assignee?: string | null }).assignee ??
    null;
  const prio = priorityLabel(node);

  return (
    <div
      className={`stx-todo-recent__row stx-todo-recent__row--${recency}`}
      onClick={() => selectNode(node.id)}
      onContextMenu={(e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY, node.id);
      }}
      role="button"
      tabIndex={0}
      title="Open details (right-click to edit)"
    >
      <div className="stx-todo-recent__topline">
        <time
          className="stx-todo-recent__ts"
          dateTime={iso || undefined}
          title={hoverTip}
        >
          {rel}
        </time>
        {recency === "new" && (
          <span
            className="stx-todo-recent__newbadge"
            title="Added in the last 24 hours"
          >
            NEW 🆕
          </span>
        )}
        {status && (
          <span
            className="stx-todo-recent__status"
            style={{
              background: status.fill,
              borderColor: status.stroke,
            }}
          >
            {node.status}
          </span>
        )}
        {project && (
          <span
            className="stx-todo-recent__project"
            style={{
              background: `hsl(${projectHue(project)} 70% 28%)`,
              borderColor: `hsl(${projectHue(project)} 70% 45%)`,
            }}
            title={`Project: ${project}`}
          >
            {project}
          </span>
        )}
        {prio && (
          <span
            className={`stx-todo-recent__prio stx-todo-recent__prio--${prio.label.toLowerCase()}`}
            title={`Priority ${prio.label}`}
          >
            {prio.label}
          </span>
        )}
      </div>
      <div className="stx-todo-recent__title">{node.title}</div>
      {agent && (
        <div className="stx-todo-recent__meta">
          <span
            className="stx-todo-recent__assignee"
            title={`Owned by ${agent}`}
          >
            👤 {agent}
          </span>
        </div>
      )}
    </div>
  );
}

/** Flat newest-first view with NEW badges + project chips. */
export function RecentView({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const [showOlder, setShowOlder] = useState(false);
  // Held module-local-ish (re-evaluated on each render) so the "ago"
  // labels stay accurate as the page stays open. AutoRefresh's poll
  // (every 5s) triggers a re-render too which keeps this fresh.
  const now = useMemo(() => new Date(), [graph]);

  const filtered = useMemo(
    () =>
      graph.nodes.filter((n) =>
        taskMatchesFilter(n as never, query, activeStatuses, activeRepos),
      ),
    [graph.nodes, query, activeStatuses, activeRepos],
  );

  const newCount = useMemo(
    () => countNewIn24h(filtered as never[], now),
    [filtered, now],
  );

  const withinLookback = useMemo(
    () =>
      showOlder
        ? filtered
        : (filterDefaultLookback(filtered as never[], now) as GraphNode[]),
    [filtered, showOlder, now],
  );

  const sorted = useMemo(
    () => sortByRecency(withinLookback as never[]) as GraphNode[],
    [withinLookback],
  );

  const olderCount = filtered.length - withinLookback.length;
  const hasAnyTimestamp = useMemo(
    () => filtered.some((n) => taskTimestamp(n as never).ts != null),
    [filtered],
  );

  return (
    <div className="stx-todo-recent">
      <div className="stx-todo-recent__titlebar">
        <span className="stx-todo-recent__heading">
          Recent — 最近のToDo (新着が上)
        </span>
        <span
          className="stx-todo-recent__count"
          title="Tasks added in the last 24 hours (using created_at, or earliest comment as fallback)"
        >
          {newCount} new in last 24h
        </span>
        {!showOlder && olderCount > 0 && (
          <button
            type="button"
            className="stx-todo-recent__show-older"
            onClick={() => setShowOlder(true)}
            title={`Show ${olderCount} tasks older than 30 days`}
          >
            Show older ({olderCount})
          </button>
        )}
        {showOlder && (
          <button
            type="button"
            className="stx-todo-recent__show-older"
            onClick={() => setShowOlder(false)}
            title="Collapse to the last 30 days"
          >
            Hide older
          </button>
        )}
      </div>
      {sorted.length === 0 && !hasAnyTimestamp && (
        <div className="stx-todo-recent__empty">
          No <code>created_at</code> timestamps yet — Recent view will populate
          as new tasks are added with <code>created_at</code> or{" "}
          <code>comments[]</code>. Existing tasks can be timestamped via{" "}
          <code>scitex-cards update &lt;id&gt; --created-at &lt;iso&gt;</code>.
        </div>
      )}
      {sorted.length === 0 && hasAnyTimestamp && (
        <div className="stx-todo-recent__empty">
          No tasks match the current filter.
        </div>
      )}
      <div className="stx-todo-recent__list">
        {sorted.map((n) => (
          <RecentRow
            key={n.id}
            node={n}
            statusColors={graph.status_colors}
            now={now}
          />
        ))}
      </div>
    </div>
  );
}
