/* recentSort.ts — TypeScript mirror of the pure-JS pure helpers shipped at
 * ``static/scitex_cards/board_v3/recentSort.js``. Used by the React side
 * (RecentView.tsx) so the Recent view's sort + classify + relative-label
 * logic is typed at the call site.
 *
 * The pure-JS sibling is the canonical source (mirrors the same pattern
 * as searchQuery.js ↔ searchQuery.ts) — unit tests at
 * ``tests/scitex_cards/test__recent_view.js`` run against the .js file via
 * ``node --test``; a Python pin test guards both files' existence and
 * the public surface so a refactor that drops one trips CI on either
 * side.
 *
 * Operator TG msg 513 (2026-06-12): "Make a Recent / 最近のカード UI."
 */

/** A task row's recency class. Drives the row's NEW badge + left-border
 * tint. */
export type RecencyClass = "new" | "recent" | "older" | "unknown";

/** Where the canonical timestamp came from — `"created_at"` is the
 * preferred SSoT field; `"comment"` is the first-comment fallback for
 * legacy rows that pre-date the `created_at` field. */
export type TimestampSource = "created_at" | "comment" | null;

export interface TaskTimestamp {
  ts: Date | null;
  source: TimestampSource;
}

/** Minimum task shape the Recent view consumes. A subset of GraphNode so
 * the helpers compose cleanly with `parseSearchQuery` / qualifier-filter
 * pipelines that may project a smaller view of each task. */
export interface RecentTask {
  id: string;
  created_at?: string | null;
  comments?: { ts?: string | null }[] | null;
}

export const HOUR_MS = 60 * 60 * 1000;
export const NEW_CUTOFF_MS = 24 * HOUR_MS;
export const RECENT_CUTOFF_MS = 72 * HOUR_MS;
export const DEFAULT_LOOKBACK_DAYS = 30;
export const DEFAULT_LOOKBACK_MS = DEFAULT_LOOKBACK_DAYS * 24 * HOUR_MS;

/** Parse an ISO-8601 timestamp string to a JS Date, or null. */
export function parseIso(value: string | null | undefined): Date | null {
  if (value == null || typeof value !== "string" || value.length === 0) {
    return null;
  }
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return null;
  return new Date(ms);
}

/** Earliest parseable comment-ts for a task, or null. */
export function earliestCommentTs(task: RecentTask): Date | null {
  const comments = Array.isArray(task.comments) ? task.comments : [];
  let earliest: Date | null = null;
  for (const c of comments) {
    const dt = parseIso(c?.ts ?? null);
    if (dt == null) continue;
    if (earliest == null || dt < earliest) earliest = dt;
  }
  return earliest;
}

/** Canonical sort timestamp: created_at → first-comment fallback. */
export function taskTimestamp(task: RecentTask): TaskTimestamp {
  const created = parseIso(task.created_at ?? null);
  if (created) return { ts: created, source: "created_at" };
  const comment = earliestCommentTs(task);
  if (comment) return { ts: comment, source: "comment" };
  return { ts: null, source: null };
}

/** Classify by recency. `now` defaults to `new Date()`. */
export function classifyRecency(task: RecentTask, now?: Date): RecencyClass {
  const { ts } = taskTimestamp(task);
  if (ts == null) return "unknown";
  const reference = now instanceof Date ? now : new Date();
  const age = reference.getTime() - ts.getTime();
  if (age < NEW_CUTOFF_MS) return "new";
  if (age < RECENT_CUTOFF_MS) return "recent";
  return "older";
}

/** Newest-first sort. Unknown-timestamp tasks sink to the bottom. Stable
 * by id within the unknown tail (so the view doesn't thrash). Returns a
 * new array; does not mutate the input. */
export function sortByRecency<T extends RecentTask>(tasks: T[]): T[] {
  const enriched = (tasks ?? []).map((t) => ({
    task: t,
    ...taskTimestamp(t),
  }));
  enriched.sort((a, b) => {
    if (a.ts == null && b.ts == null) {
      return String(a.task.id ?? "").localeCompare(String(b.task.id ?? ""));
    }
    if (a.ts == null) return 1;
    if (b.ts == null) return -1;
    return b.ts.getTime() - a.ts.getTime();
  });
  return enriched.map((e) => e.task);
}

/** Count of tasks classified as "new" (< 24h). Drives the title-bar
 * badge `N new in last 24h`. */
export function countNewIn24h(tasks: RecentTask[], now?: Date): number {
  let n = 0;
  for (const t of tasks ?? []) {
    if (classifyRecency(t, now) === "new") n++;
  }
  return n;
}

/** "2h ago" / "yesterday" / "3 days ago" / "2 months ago" style label. */
export function relativeTimestamp(ts: Date | null, now?: Date): string {
  if (ts == null) return "no timestamp";
  const reference = now instanceof Date ? now : new Date();
  const ms = reference.getTime() - ts.getTime();
  if (ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day === 1) return "yesterday";
  if (day < 30) return `${day} days ago`;
  const months = Math.floor(day / 30);
  if (months < 12) return months === 1 ? "1 month ago" : `${months} months ago`;
  const years = Math.floor(day / 365);
  return years === 1 ? "1 year ago" : `${years} years ago`;
}

/** Filter to the default 30-day lookback. Tasks WITHOUT a usable
 * timestamp are KEPT (they sink via sortByRecency, but should still be
 * surfaced as candidates for backfill). */
export function filterDefaultLookback<T extends RecentTask>(
  tasks: T[],
  now?: Date,
): T[] {
  const reference = now instanceof Date ? now : new Date();
  const cutoff = reference.getTime() - DEFAULT_LOOKBACK_MS;
  return (tasks ?? []).filter((t) => {
    const { ts } = taskTimestamp(t);
    if (ts == null) return true;
    return ts.getTime() >= cutoff;
  });
}
