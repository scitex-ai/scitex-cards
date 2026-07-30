/* recentSort.js — pure helpers for the Recent view (newest-first triage
 * surface). Operator TG msg 513 (2026-06-12): "Make a Recent / 最近のToDo
 * UI. There are many ToDos now — I want to see at a glance when something
 * new comes in."
 *
 * Design framing (dogfooding loop, lead-aligned): the Recent view is the
 * fleet's FEEDBACK INTAKE surface, not just sort-by-date. When a paper or
 * a project drops 3 new tasks in a morning, the operator scans the
 * Recent view, eyeballs the project chips, and asks "what's the
 * abstractable pattern?". So the row carries the project chip + a NEW
 * badge that fades by recency, in addition to the timestamp.
 *
 * Pure functions, no DOM, no React, no fetch — node --test friendly. The
 * React side (frontend/src/recentSort.ts) re-exports the same surface
 * with TS types; tests at tests/scitex_cards/test__recent_view.js cover
 * the pure-fn shape and a Python pin test guards the file's existence.
 *
 * Data contract (matches the graph wire from handlers/graph.py):
 *   - `created_at` (ISO-8601 UTC) is the canonical newest-first sort key.
 *   - When `created_at` is absent, fall back to the EARLIEST comment's
 *     `ts` (first time the task showed activity).
 *   - When both are absent, treat as "unknown" and sink to the bottom.
 *
 * NEW classification (the visual "huh, that's new" cue):
 *   - < 24h    → "new"      (bright orange NEW 🆕 badge on the row)
 *   - 24-72h   → "recent"   (subtle yellow row-left-border tint)
 *   - >= 72h   → "older"    (plain row)
 *   - unknown  → "unknown"  (no badge, plain row)
 *
 * "Default lookback": 30 days. The Recent view shows tasks newer than
 * NOW - 30d by default and a "Show older" link reveals the rest.
 */
"use strict";

/** Hours, expressed in milliseconds. */
const HOUR_MS = 60 * 60 * 1000;
const NEW_CUTOFF_MS = 24 * HOUR_MS;
const RECENT_CUTOFF_MS = 72 * HOUR_MS;
const DEFAULT_LOOKBACK_DAYS = 30;
const DEFAULT_LOOKBACK_MS = DEFAULT_LOOKBACK_DAYS * 24 * HOUR_MS;

/** Parse an ISO-8601 timestamp string to a JS Date, or null if unparseable.
 *
 * Defensive: a non-string / empty / unparseable input returns null so the
 * caller can sink the task to the bottom rather than throwing.
 */
function parseIso(value) {
  if (value == null || typeof value !== "string" || value.length === 0) {
    return null;
  }
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return null;
  return new Date(ms);
}

/** Earliest comment timestamp for a task, or null if no parseable comment.
 *
 * Mirrors `_model.py`'s `comments[]` shape: list of {ts, author, text}.
 * The earliest comment is the closest proxy we have to "when did this
 * task first show activity" when `created_at` is absent on a legacy row.
 */
function earliestCommentTs(task) {
  // PREFER the server-derived scalar. /graph ships comments[] at 8.4 MB of a
  // 19.8 MB payload and is being reduced to these scalars; this reads the
  // cheap field first and keeps the scan only as a fallback for a payload
  // that predates it.
  //
  // The scalar is POSITIONAL (the thread opener) while the scan below is
  // MIN(ts). Measured 2026-07-30 on 1,766 cards with comments: they agree in
  // every case, and none had a missing ts. They agree because the thread is
  // append-only — nothing enforces that ts ascends, so if a backfill or a
  // clock-skewed writer ever changes that, this preference changes the sort
  // key silently. See handlers/_comment_digest.py for the same note and the
  // condition to re-check.
  const scalar = parseIso(task && task.first_comment_ts);
  if (scalar) return scalar;

  const comments = task && Array.isArray(task.comments) ? task.comments : [];
  let earliest = null;
  for (const c of comments) {
    const dt = parseIso(c && c.ts);
    if (dt == null) continue;
    if (earliest == null || dt < earliest) earliest = dt;
  }
  return earliest;
}

/** Resolve the canonical sort timestamp for a task.
 *
 * Returns the Date and a `source` tag ("created_at" | "comment" | null).
 * The source tag lets the FE render a discreet "(from first comment)"
 * hover note so the operator knows the timestamp is a fallback.
 */
function taskTimestamp(task) {
  const created = parseIso(task && task.created_at);
  if (created) return { ts: created, source: "created_at" };
  const comment = earliestCommentTs(task);
  if (comment) return { ts: comment, source: "comment" };
  return { ts: null, source: null };
}

/** Classify a task by recency (relative to `now`).
 *
 * Returns one of "new" | "recent" | "older" | "unknown". The Recent view
 * paints "new" rows with an orange NEW 🆕 badge, "recent" rows with a
 * subtle yellow left-border tint, "older" plain, "unknown" plain (no
 * badge — they're the legacy backfill case, not actionable as "new").
 */
function classifyRecency(task, now) {
  const { ts } = taskTimestamp(task);
  if (ts == null) return "unknown";
  const reference = now instanceof Date ? now : new Date();
  const age = reference.getTime() - ts.getTime();
  if (age < NEW_CUTOFF_MS) return "new";
  if (age < RECENT_CUTOFF_MS) return "recent";
  return "older";
}

/** Sort tasks newest-first by canonical timestamp.
 *
 * Order:
 *   1. Tasks with a usable timestamp, newest first (created_at preferred,
 *      first-comment fallback).
 *   2. Unknown-timestamp tasks LAST, stable by id (so the view doesn't
 *      thrash when an unrelated task gains a created_at).
 *
 * Pure: does not mutate the input array (returns a new array).
 */
function sortByRecency(tasks) {
  const enriched = (tasks || []).map((t) => ({
    task: t,
    ...taskTimestamp(t),
  }));
  enriched.sort((a, b) => {
    if (a.ts == null && b.ts == null) {
      // Stable secondary by id so the order is deterministic.
      return String(a.task.id || "").localeCompare(String(b.task.id || ""));
    }
    if (a.ts == null) return 1; // unknown sinks
    if (b.ts == null) return -1;
    // Newest first.
    return b.ts.getTime() - a.ts.getTime();
  });
  return enriched.map((e) => e.task);
}

/** Count tasks classified as "new" (< 24h) — drives the title-bar badge. */
function countNewIn24h(tasks, now) {
  let n = 0;
  for (const t of tasks || []) {
    if (classifyRecency(t, now) === "new") n++;
  }
  return n;
}

/** "2h ago" / "yesterday" / "3 days ago" / "2 months ago" style label.
 *
 * Compact, human-scannable. Always English (the title bar carries
 * "最近のToDo" so the per-row label stays compact).
 */
function relativeTimestamp(ts, now) {
  if (ts == null) return "no timestamp";
  const reference = now instanceof Date ? now : new Date();
  const ms = reference.getTime() - ts.getTime();
  // Future (clock skew or scheduled): just say "just now".
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

/** Filter to the default 30-day lookback window.
 *
 * Tasks WITHOUT a usable timestamp are kept (they'll sink to the bottom
 * via `sortByRecency`) — hiding them would silently disappear legacy
 * backfill rows and confuse the operator. The "Show older" toggle is
 * about hiding *known-old* rows, not unknown-age rows.
 */
function filterDefaultLookback(tasks, now) {
  const reference = now instanceof Date ? now : new Date();
  const cutoff = reference.getTime() - DEFAULT_LOOKBACK_MS;
  return (tasks || []).filter((t) => {
    const { ts } = taskTimestamp(t);
    if (ts == null) return true; // unknown timestamps always kept
    return ts.getTime() >= cutoff;
  });
}

module.exports = {
  parseIso,
  earliestCommentTs,
  taskTimestamp,
  classifyRecency,
  sortByRecency,
  countNewIn24h,
  relativeTimestamp,
  filterDefaultLookback,
  // Constants (exported for tests + the TS mirror).
  HOUR_MS,
  NEW_CUTOFF_MS,
  RECENT_CUTOFF_MS,
  DEFAULT_LOOKBACK_DAYS,
  DEFAULT_LOOKBACK_MS,
};
