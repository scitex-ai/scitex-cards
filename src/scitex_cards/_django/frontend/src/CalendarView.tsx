/** CalendarView — the 4th LAYOUT option (operator TG 13295, relayed by
 * lead a2a 510a58d4). Tasks land on a month grid by their `deadline_next`
 * → `deadline` → `last_activity` precedence (see `calendarDate.ts`).
 *
 * Floor version:
 *   - Current month grid (7 cols × 6 rows = 42 cells).
 *   - Header row: weekday labels (Sun-Sat).
 *   - Prev / Next month buttons top-left; "Today" pill top-right.
 *   - Each day cell: date number + up to 4 task chips ("+N more" overflow).
 *   - Task chip = [<status-color-dot> <title-truncated>], clickable to
 *     open the NodeDetailPanel drawer (reuses TableView's selectNode pattern).
 *   - Today cell gets a subtle accent border.
 *   - Past in-month days get muted text; weekend bg shift.
 *
 * Deferred (operator-future asks, NOT this PR):
 *   - Drag-and-drop reschedule between days.
 *   - Week / day view granularity.
 *   - Inline edit on day-cell click.
 *   - Recurring-deadline rendering beyond the server-provided
 *     `deadline_next` next-occurrence value.
 */

import { useMemo, useState } from "react";
import {
  MONTH_NAMES,
  WEEKDAY_NAMES,
  monthGridDays,
  shiftMonth,
  tasksByDate,
} from "./calendarDate";
import { taskMatchesFilter, useBoardStore } from "./store/useBoardStore";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";

/** Per-cell visible task cap. Operator-friendly density — keep the grid
 * scannable; overflow rolls into a "+N more" chip that still opens the
 * matching day-section so the operator can drill in via the detail
 * drawer one task at a time. */
const VISIBLE_CHIPS_PER_CELL = 4;

/** A small status dot (drawn from `status_colors`) prefixing the chip
 * title. Falls back to a neutral grey when an unknown status slips through
 * (defensive — store CRUD validates the closed set). */
function StatusDot({ color }: { color: StatusColor | undefined }) {
  return (
    <span
      className="stx-cards-cal__dot"
      aria-hidden="true"
      style={{
        background: color?.fill ?? "var(--stx-border-strong)",
        borderColor: color?.stroke ?? "var(--stx-border-strong)",
      }}
    />
  );
}

/** One clickable task chip inside a day cell. Clicking opens the detail
 * drawer (mirroring TableView's selectNode flow). Right-click opens the
 * existing context menu so status-set / edit / delete are reachable too. */
function TaskChip({
  node,
  statusColors,
}: {
  node: GraphNode;
  statusColors: Record<string, StatusColor>;
}) {
  const selectNode = useBoardStore((s) => s.selectNode);
  const openMenu = useBoardStore((s) => s.openMenu);
  const c = statusColors[node.status];
  return (
    <button
      type="button"
      className="stx-cards-cal__chip"
      onClick={(e) => {
        e.stopPropagation();
        selectNode(node.id);
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openMenu(e.clientX, e.clientY, node.id);
      }}
      title={`${node.title}\nstatus: ${node.status}${node.repo ? `\nrepo: ${node.repo}` : ""}`}
    >
      <StatusDot color={c} />
      <span className="stx-cards-cal__chip-title">{node.title}</span>
    </button>
  );
}

interface DayCellData {
  date: Date;
  key: string;
  inMonth: boolean;
  isToday: boolean;
  weekday: number;
  day: number;
  tasks: GraphNode[];
}

/** One day cell. Renders the date number + up to N chips + "+M more".
 * The chips are individually clickable; clicking the cell background is a
 * no-op for the floor (inline-create is deferred). */
function DayCell({
  cell,
  statusColors,
  todayMidnight,
}: {
  cell: DayCellData;
  statusColors: Record<string, StatusColor>;
  todayMidnight: number;
}) {
  const visible = cell.tasks.slice(0, VISIBLE_CHIPS_PER_CELL);
  const overflow = cell.tasks.length - visible.length;
  const past = cell.inMonth && cell.date.getTime() < todayMidnight;
  const weekend = cell.weekday === 0 || cell.weekday === 6;
  const classes = [
    "stx-cards-cal__cell",
    cell.inMonth ? "" : "stx-cards-cal__cell--out",
    cell.isToday ? "stx-cards-cal__cell--today" : "",
    past ? "stx-cards-cal__cell--past" : "",
    weekend ? "stx-cards-cal__cell--weekend" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      className={classes}
      role="gridcell"
      aria-label={`${cell.date.toDateString()} — ${cell.tasks.length} task${
        cell.tasks.length === 1 ? "" : "s"
      }`}
    >
      <div className="stx-cards-cal__cell-head">
        <span className="stx-cards-cal__cell-day">{cell.day}</span>
        {cell.isToday && (
          <span className="stx-cards-cal__cell-today" title="Today">
            today
          </span>
        )}
      </div>
      <div className="stx-cards-cal__chips">
        {visible.map((t) => (
          <TaskChip key={t.id} node={t} statusColors={statusColors} />
        ))}
        {overflow > 0 && (
          <span
            className="stx-cards-cal__more"
            title={`${overflow} more task${overflow === 1 ? "" : "s"} on this day`}
          >
            +{overflow} more
          </span>
        )}
      </div>
    </div>
  );
}

/** The 4th LAYOUT — month grid. Honors the toolbar's search + status +
 * repo filters via `taskMatchesFilter`, so typing
 * `project:paper-scitex-clew status:pending` narrows the calendar the
 * same way it narrows Graph / Table / Recent. */
export function CalendarView({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);

  // Month-cursor lives in component state — view-only ephemeral nav,
  // does NOT belong in the persistent board store (the operator's
  // expectation is "open Calendar → see today's month"; on close /
  // re-open we re-anchor to now via `useState`'s lazy init).
  const [{ year, monthIndex }, setCursor] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), monthIndex: now.getMonth() };
  });

  const filtered = useMemo(
    () =>
      graph.nodes.filter((n) =>
        taskMatchesFilter(n, query, activeStatuses, activeRepos),
      ),
    [graph.nodes, query, activeStatuses, activeRepos],
  );

  // Group ALL filtered tasks across all months once — the lookup is O(1)
  // per cell, and switching months stays cheap (no recompute).
  const byDate = useMemo(() => tasksByDate(filtered), [filtered]);

  const now = useMemo(() => new Date(), [graph]);
  const todayMidnight = useMemo(() => {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }, [now]);

  const cells: DayCellData[] = useMemo(() => {
    const grid = monthGridDays(year, monthIndex, now);
    return grid.map((c) => ({
      ...c,
      tasks: byDate.get(c.key) ?? [],
    }));
  }, [year, monthIndex, now, byDate]);

  // Aggregate counts for the title bar — "N tasks scheduled this month"
  // counts only IN-MONTH cells so the operator's intuition ("June has X
  // tasks") matches what they read.
  const monthCount = useMemo(
    () =>
      cells.filter((c) => c.inMonth).reduce((n, c) => n + c.tasks.length, 0),
    [cells],
  );

  const undated = useMemo(
    () =>
      filtered.length -
      Array.from(byDate.values()).reduce((n, l) => n + l.length, 0),
    [filtered.length, byDate],
  );

  const onPrev = () => {
    const next = shiftMonth(year, monthIndex, -1);
    setCursor(next);
  };
  const onNext = () => {
    const next = shiftMonth(year, monthIndex, 1);
    setCursor(next);
  };
  const onToday = () => {
    const t = new Date();
    setCursor({ year: t.getFullYear(), monthIndex: t.getMonth() });
  };

  return (
    <div className="stx-cards-cal">
      <div className="stx-cards-cal__bar">
        <div className="stx-cards-cal__nav">
          <button
            type="button"
            className="stx-cards-cal__navbtn"
            onClick={onPrev}
            aria-label="Previous month"
            title="Previous month"
          >
            ‹
          </button>
          <button
            type="button"
            className="stx-cards-cal__navbtn"
            onClick={onNext}
            aria-label="Next month"
            title="Next month"
          >
            ›
          </button>
          <span className="stx-cards-cal__title" aria-live="polite">
            {MONTH_NAMES[monthIndex]} {year}
          </span>
        </div>
        <div className="stx-cards-cal__bar-right">
          <span
            className="stx-cards-cal__count"
            title="Tasks placed on a day in this month (deadline → last_activity)"
          >
            {monthCount} scheduled this month
          </span>
          {undated > 0 && (
            <span
              className="stx-cards-cal__undated"
              title={`${undated} matching task${undated === 1 ? "" : "s"} have no deadline or last_activity and are not rendered on the calendar`}
            >
              {undated} undated
            </span>
          )}
          <button
            type="button"
            className="stx-cards-cal__today-pill"
            onClick={onToday}
            title="Snap back to the current month"
          >
            Today
          </button>
        </div>
      </div>
      <div className="stx-cards-cal__weekdays" role="row">
        {WEEKDAY_NAMES.map((w, i) => (
          <div
            key={w}
            className={`stx-cards-cal__weekday${i === 0 || i === 6 ? " stx-cards-cal__weekday--weekend" : ""}`}
            role="columnheader"
          >
            {w}
          </div>
        ))}
      </div>
      <div className="stx-cards-cal__grid" role="grid" aria-label="Month grid">
        {cells.map((c) => (
          <DayCell
            key={c.key}
            cell={c}
            statusColors={graph.status_colors}
            todayMidnight={todayMidnight}
          />
        ))}
      </div>
    </div>
  );
}
