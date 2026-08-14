/** Shared types for the scitex-todo board. Mirror the backend graph payload. */

/** One entry in a task's append-only comment thread. `ts` is an ISO-8601 UTC
 * timestamp and `author` the commenter, both stamped server-side. */
export interface TaskComment {
  ts: string;
  author: string;
  text: string;
}

/** Task kind — discriminator across the three row shapes the board renders.
 * Closed validated set (matches `VALID_KINDS` in `_model.py`); absence over
 * the wire (null) = `"task"` (the default). Extensible to `"ci"` etc. when
 * task #15 wires GH-Actions rows; add the variant here and in the Python
 * validator together.
 *
 *  - "task"     — ordinary, human-updated row (default).
 *  - "compute"  — external compute job; status auto-updated by a writer.
 *                 ⚙ glyph + KV table in the drawer. ADR-0002.
 *  - "decision" — operator/agent decision other tasks depends_on. Body
 *                 lives in `tasks/<id>/adr.md` (1:1 with the ADR
 *                 convention). When `status` flips to `done`, dependents
 *                 auto-unblock via the dep-graph wire (no new machinery).
 *                 ⚖️ glyph + LOUD halo when blocker == "operator-decision".
 *                 ADR-0003 (this PR). North-star pillar #4.
 */
export type TaskKind = "task" | "compute" | "decision";

/** Blocker variant on a `status: "blocked"` row — what kind of thing is
 * blocking it. Operator's enumeration (TG 9524), closed validated set
 * (matches `VALID_BLOCKERS` in `_model.py`). ORTHOGONAL to `TaskKind`:
 * a kind=decision row usually has blocker=operator-decision but can have
 * any variant.
 *
 *  - "compute"           — 計算リソース    — waiting on a kind=compute row
 *  - "dep"               — 依存            — waiting on another task
 *  - "operator-decision" — ユーザー判断    — waiting on the operator (LOUD)
 *  - "agent-wait"        — 他エージェント待ち — waiting on a specific agent action
 */
export type BlockerKind =
  "compute" | "dep" | "operator-decision" | "agent-wait";

export interface GraphNode {
  id: string;
  title: string;
  status: string;
  priority: number | null;
  note: string | null;
  repo: string | null;
  /** Id of the node this task nests under. `null` (or absent) = top-level.
   *  Drives the nested-graph drill-down: clicking a node WITH children (any
   *  task whose `parent` equals this node's id) re-renders the canvas to that
   *  child subgraph; a breadcrumb navigates back. */
  parent: string | null;
  /** Append-only comment thread (oldest first). Always present (may be []). */
  comments: TaskComment[];

  /** Task kind. `null` (absent over the wire) is equivalent to `"task"`.
   *  `"compute"` marks a row representing an external compute job whose
   *  status is updated by an automated writer (north-star pillar #1; full
   *  design in `tasks/proj-scitex-todo-compute-state-deps/description.md`).
   */
  kind: TaskKind | null;
  /** Opaque compute-job identifier (slurm id, GH Actions run id, k8s job, …).
   *  Only meaningful when `kind === "compute"`. */
  job_id: string | null;
  /** Where the compute job runs (`spartan`, `mba`, `github`, `nas`, …).
   *  Only meaningful when `kind === "compute"`. */
  host: string | null;
  /** Shell invocation / pipeline. Long; the board truncates to ~100 chars
   *  on the node tooltip and shows the full string on click + hover in the
   *  NodeDetailPanel KV table. Only meaningful when `kind === "compute"`. */
  command: string | null;
  /** ISO-8601 timestamp when the writer observed the job started. Only
   *  meaningful when `kind === "compute"`. */
  started_at: string | null;
  /** ISO-8601 timestamp when the writer observed completion (success OR
   *  failure). Only meaningful when `kind === "compute"`. */
  finished_at: string | null;

  /** Blocker variant for a `status === "blocked"` row — names what is
   *  blocking it (compute / dep / operator-decision / agent-wait). null
   *  on non-blocked rows AND on blocked rows where the variant hasn't
   *  been named yet (soft-degrade — FE renders the generic 🚧 with no
   *  extra badge in that case). ADR-0004. */
  blocker: BlockerKind | null;

  /** ISO-8601 explicit deadline (YYYY-MM-DD or full ts). Drives the
   *  Calendar view's primary date assignment (operator TG 13295). Already
   *  emitted by handlers/graph.py — declared here so the FE can consume
   *  it without ad-hoc `as never` casts. May be absent. */
  deadline?: string | null;
  /** Server-computed next occurrence (recurring + multi expanded). Calendar
   *  prefers this over `deadline` when present, since it already encodes
   *  "closest upcoming occurrence". Emitted by handlers/graph.py via
   *  `_compute_deadline_next`. May be absent. */
  deadline_next?: string | null;
  /** ISO-8601 last-activity ts (date portion drives the Calendar view's
   *  fallback bucket when no deadline is set). Emitted by handlers/
   *  graph.py. May be absent. */
  last_activity?: string | null;
}

export type EdgeKind = "depends_on" | "blocks";

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface StatusColor {
  fill: string;
  stroke: string;
  dashed: boolean;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  status_colors: Record<string, StatusColor>;
  // No `mermaid` field: the server stopped shipping it (38.4% of the /graph
  // payload, and no reader). Graph views build their own source from `edges`
  // + `status_colors`, which is what lets them respect the filter set.
  store_path: string;
  task_count: number;
}
