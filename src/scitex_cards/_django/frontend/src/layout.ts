/** Dagre layout: turn the backend graph payload into React Flow nodes/edges.
 *
 * Only `depends_on` edges drive the top->bottom DAG layout (dependencies above
 * dependents). `blocks` edges are drawn as inhibition arrows but excluded from
 * layout so they don't fight the ranking. Within a rank, nodes are ordered by
 * `priority` (lower = earlier) when present.
 *
 * Uncategorized tasks (see `partitionNodes`) are excluded from the graph
 * entirely; they are rendered separately in the bordered staging pool.
 *
 * Nested-graph drill-down: at any given moment the canvas shows ONE scope —
 * either the top-level (`scope: null` → nodes whose `parent` is null/absent)
 * or a parent's children (`scope: <parent-id>` → nodes whose `parent`
 * equals that id). `buildFlow` and `partitionNodes` are both
 * scope-parameterized so the same renderer drives every level. Edges
 * between visible nodes are kept; edges that cross the scope boundary are
 * dropped (siblings only). See `scopeNodes` and `nodeHasChildren`.
 */

import { createElement, type CSSProperties, type ReactNode } from "react";
import dagre from "dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";
import { INHIBITION_EDGE_TYPE } from "./InhibitionEdge";

const NODE_W = 200;
const NODE_H = 60;

/** Edge colors.
 *
 * `depends_on` (→) is the default neutral arrow; `blocks` (⊣) is rendered by
 * the custom `InhibitionEdge` component (full-length solid line + perpendicular
 * tee at the target endpoint, NO text label — see InhibitionEdge.tsx for why
 * we own the rendering instead of using `markerEnd: url(#…)`). Both edges share
 * the same body weight so they read as siblings — only the color and the
 * end-cap (arrowhead vs tee) distinguish them.
 */
export const EDGE_COLOR_DEPENDS = "#607d8b";
export const EDGE_COLOR_BLOCKS = "#c62828";

export function nodeStyle(color: StatusColor | undefined): CSSProperties {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  return {
    background: c.fill,
    border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
    borderRadius: 8,
    padding: "8px 10px",
    width: NODE_W,
    color: "#222",
    fontSize: 12,
    textAlign: "center",
  };
}

/** Restrict `graph.nodes` to those visible at the given drill-down scope.
 *
 * `scope === null` ⇒ top-level: nodes whose `parent` is null/undefined.
 * `scope === <id>` ⇒ children of <id>: nodes whose `parent` equals <id>.
 *
 * A node whose `parent` references an id not present in `graph.nodes` is
 * "orphaned"; we hoist it to the top level so it stays reachable even if
 * the operator deletes the umbrella mid-edit (same lenient stance as edges
 * to unknown ids, which the backend drops).
 */
export function scopeNodes(
  graph: GraphPayload,
  scope: string | null,
): GraphNode[] {
  const ids = new Set(graph.nodes.map((n) => n.id));
  return graph.nodes.filter((n) => {
    const parent = n.parent ?? null;
    if (scope === null) {
      // Top-level: explicit `parent: null` OR an orphaned reference.
      return parent === null || !ids.has(parent);
    }
    return parent === scope;
  });
}

/** How many direct child tasks does `node` have (any `parent === node.id`)?
 *
 * Powers the parent-node drill-down affordance: BEFORE clicking, the user
 * sees the count baked into the rendered label (a "▸N" badge) so they can
 * predict the click will descend into a subgraph instead of opening the
 * markdown drawer. A non-zero count also flips the click-routing branch.
 */
export function nodeChildCount(graph: GraphPayload, nodeId: string): number {
  let count = 0;
  for (const n of graph.nodes) {
    if (n.parent === nodeId) count += 1;
  }
  return count;
}

/** Does `node` have at least one child task (any `parent === node.id`)?
 *
 * Used by the click handler to decide between drill-down (has children) and
 * the markdown detail drawer (leaf node — existing #9 behavior).
 */
export function nodeHasChildren(graph: GraphPayload, nodeId: string): boolean {
  return nodeChildCount(graph, nodeId) > 0;
}

/** Visual decoration applied ON TOP of `nodeStyle` for nodes that have
 * children — they DRILL IN on click rather than open the detail drawer.
 *
 * Goal: one glance tells drill-vs-detail. The combination here is
 * deliberately layered (operator UX feedback: prior 3px solid border + halo
 * was too subtle — a parent looked just like a slightly-thicker leaf):
 *   - A 5px DOUBLE border (vs the leaf's 2px solid) gives parents a visibly
 *     different SHAPE — the doubled rim reads as "container / boundary"
 *     without you having to read the title or the badge.
 *   - A stacked-card shadow pile (offset box-shadows, one per child up to 5)
 *     plants the silhouette of a stack of cards BEHIND the front face.
 *   - A purple drill-in halo hugs the front card.
 *   - Bold label weight nudges parents away from looking like flat leaves.
 *   - A subtle vertical gradient overlays the status fill so the card reads
 *     as "layered" rather than flat — depth cue at the surface level.
 *
 * Status color (fill / stroke) is preserved from `nodeStyle()` so the
 * lifecycle signal (pending / done / blocked / goal …) still reads.
 */
export function parentNodeStyle(
  base: CSSProperties,
  kids = 1,
  color?: StatusColor,
): CSSProperties {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  // Render a GROUP as a stacked pile: one offset card silhouette per child,
  // capped at 5, each a fill rect + a thin stroke edge (paired box-shadows).
  // A 4px diagonal shift per layer reads unmistakably as "a stack of cards".
  const layers = Math.min(Math.max(kids, 1), 5);
  const stack: string[] = [];
  for (let i = 1; i <= layers; i++) {
    const o = i * 4;
    stack.push(`${o}px ${o}px 0 0 ${c.stroke}`);
    stack.push(`${o}px ${o}px 0 -2px ${c.fill}`);
  }
  // Purple drill-in halo hugs the front card (offset 0, listed first = on top).
  stack.unshift("0 0 0 3px rgba(155, 127, 214, 0.55)");
  return {
    ...base,
    // 5px DOUBLE border — `double` requires ≥3px to render the two parallel
    // strokes; 5px gives a clean, visibly-different rim from the leaf's 2px
    // solid line. This is the cheapest, most-distinctive shape signal.
    borderWidth: 5,
    borderStyle: "double",
    boxShadow: stack.join(", "),
    fontWeight: 600,
    // Subtle vertical gradient over the existing fill — top edge a touch
    // lighter, bottom a touch darker — so the card reads as a 3-D surface
    // rather than a flat tile, reinforcing the "container with depth" cue.
    backgroundImage:
      "linear-gradient(180deg, rgba(255,255,255,0.18) 0%, rgba(0,0,0,0.06) 100%)",
    // Make room ABOVE the title for the absolute-positioned pill badge that
    // sits at the top-right corner (see parentLabel in buildFlow).
    paddingTop: 14,
    position: "relative",
  };
}

/** Build the rich label element rendered inside a parent (drill-down) node.
 *
 * Why a React element instead of a plain string? The default React Flow
 * label is plain text, so a status pill can't ride along with the title.
 * Returning a JSX subtree lets us:
 *   - place a colored "N ↓" PILL BADGE in the top-right corner, absolute-
 *     positioned via CSS (`.stx-cards-node__badge`)
 *   - prefix the title with a "⊞" expand glyph (universal "container /
 *     drill in" icon — not theme- or font-dependent like a folder emoji)
 *   - set a NATIVE `title` attribute on the inner span so the browser
 *     tooltip says "Drill into <title> (N children)" on hover, matching
 *     the pool-side affordance.
 */
function parentLabel(
  title: string,
  kids: number,
  suffix: string,
  blocked: boolean,
  blockerId: string | null,
  compute: ComputeMeta | null,
  decision: DecisionMeta | null,
): ReactNode {
  const tip = decision
    ? composeDecisionTooltip(title, decision, blocked, blockerId) +
      `\n(${kids} ${kids === 1 ? "child" : "children"} — click to drill in)`
    : compute
    ? composeComputeTooltip(title, compute, blocked, blockerId) +
      `\n(${kids} ${kids === 1 ? "child" : "children"} — click to drill in)`
    : blocked
    ? `Drill into ${title} (${kids} ${
        kids === 1 ? "child" : "children"
      }) — this branch is BLOCKED${blockerId ? ` by ${blockerId}` : ""}`
    : `Drill into ${title} (${kids} ${kids === 1 ? "child" : "children"})`;
  return createElement(
    "span",
    { className: "stx-cards-node__label", title: tip },
    createElement(
      "span",
      { className: "stx-cards-node__badge", "aria-label": tip },
      `${kids} ↓`,
    ),
    decision
      ? createElement(
          "span",
          {
            className: "stx-cards-node__decision",
            "aria-label": "decision node",
            title: "Decision node — body in tasks/<id>/adr.md (kind: decision)",
          },
          "⚖️ ",
        )
      : null,
    compute
      ? createElement(
          "span",
          {
            className: "stx-cards-node__compute",
            "aria-label": "compute job",
            title: "Compute job — externally-updated row (kind: compute)",
          },
          "⚙ ",
        )
      : null,
    blocked
      ? createElement(
          "span",
          {
            className: "stx-cards-node__blocked",
            "aria-label": "blocked",
            title: "Blocked — see Blockers section in the detail drawer",
          },
          "🚧 ",
        )
      : null,
    createElement(
      "span",
      { className: "stx-cards-node__glyph", "aria-hidden": "true" },
      "⊞ ",
    ),
    `${title}${suffix}`,
    blocked && blockerId
      ? createElement(
          "span",
          {
            className: "stx-cards-node__blocked-by",
            title: `Blocked by ${blockerId}`,
          },
          `← ${truncateId(blockerId)}`,
        )
      : null,
    decision && decision.impact > 0
      ? createElement(
          "span",
          {
            className: `stx-cards-node__decision-impact${
              decision.impact >= 5
                ? " stx-cards-node__decision-impact--high"
                : ""
            }`,
            title: `Resolving this decision will auto-unblock ${decision.impact} task${
              decision.impact === 1 ? "" : "s"
            }`,
          },
          `⚖️ unblocks ${decision.impact}`,
        )
      : null,
    // Hover-hint pill: invisible until the parent node is hovered, then
    // fades in to spell out the action explicitly. Operator UX 2026-06-06
    // TG 245 ("ドリルというか視覚的に持ってくれるといいな…誰にでもわかる
    // ように"). Pairs with the removal of the -1deg hover tilt — the
    // hover affordance is now a CLEAN, in-place text reveal instead of
    // a card rotation that obscured the graph behind.
    createElement(
      "span",
      {
        className: "stx-cards-node__hover-hint",
        "aria-hidden": "true",
      },
      "⊞ Drill in",
    ),
  );
}

/** Label for a leaf node — plain text content but wrapped in a span with a
 * `title` attr so the browser tooltip explicitly says "Open details for
 * <title>", giving the operator a hover-time confirmation that this click
 * opens the markdown drawer (not drill-down).
 *
 * When the task is `status: blocked`, a leading "🚧" glyph is prepended so the
 * board reads at a glance which threads are stuck (the operator's UX request
 * 2026-06-06: "ブロッカーが何かわからないので、card にブロッカー可視化"). The
 * tooltip also flags it as blocked so a hover confirms what's wrong without
 * opening the drawer. */
function leafLabel(
  title: string,
  suffix: string,
  blocked: boolean,
  blockerId: string | null,
  compute: ComputeMeta | null,
  decision: DecisionMeta | null,
): ReactNode {
  const tip = decision
    ? composeDecisionTooltip(title, decision, blocked, blockerId)
    : compute
      ? composeComputeTooltip(title, compute, blocked, blockerId)
      : blocked
        ? `BLOCKED — ${title}${
            blockerId ? ` (blocked by ${blockerId})` : ""
          } (open details to see the full chain)`
        : `Open details for ${title}`;
  return createElement(
    "span",
    {
      className: "stx-cards-node__label",
      title: tip,
    },
    decision
      ? createElement(
          "span",
          {
            className: "stx-cards-node__decision",
            "aria-label": "decision node",
            title: "Decision node — body lives in tasks/<id>/adr.md (kind: decision)",
          },
          "⚖️ ",
        )
      : null,
    compute
      ? createElement(
          "span",
          {
            className: "stx-cards-node__compute",
            "aria-label": "compute job",
            title: "Compute job — externally-updated row (kind: compute)",
          },
          "⚙ ",
        )
      : null,
    blocked
      ? createElement(
          "span",
          {
            className: "stx-cards-node__blocked",
            "aria-label": "blocked",
          },
          "🚧 ",
        )
      : null,
    `${title}${suffix}`,
    blocked && blockerId
      ? createElement(
          "span",
          {
            className: "stx-cards-node__blocked-by",
            title: `Blocked by ${blockerId}`,
          },
          `← ${truncateId(blockerId)}`,
        )
      : null,
    decision && decision.impact > 0
      ? createElement(
          "span",
          {
            className: `stx-cards-node__decision-impact${
              decision.impact >= 5
                ? " stx-cards-node__decision-impact--high"
                : ""
            }`,
            title: `Resolving this decision will auto-unblock ${decision.impact} task${
              decision.impact === 1 ? "" : "s"
            }`,
          },
          `⚖️ unblocks ${decision.impact}`,
        )
      : null,
  );
}

/** Subset of compute-row metadata used by the node label + tooltip. Mirrors
 * the fields validated by `_model._validate_tasks` (job_id / host / command
 * / started_at / finished_at). Always null on a `kind: "task"` row (the
 * default) so the label-builder can skip the compute affordances with a
 * single `compute ? …` ternary. */
export interface ComputeMeta {
  job_id: string | null;
  host: string | null;
  command: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** Build the tooltip for a compute node — short, multi-line, ~100-char
 * command-truncation per lead a2a `2c7a431d` ("~100 chars truncation +
 * full-on-hover is sensible"). The full command + all metadata are rendered
 * as a KV table in the NodeDetailPanel when the operator clicks the node;
 * this is the at-a-glance summary on the canvas. */
function composeComputeTooltip(
  title: string,
  c: ComputeMeta,
  blocked: boolean,
  blockerId: string | null,
): string {
  const bits: string[] = [];
  if (c.host) bits.push(`host=${c.host}`);
  if (c.job_id) bits.push(`job=${c.job_id}`);
  // Slice ISO timestamps to YYYY-MM-DDTHH:MM for compactness; the drawer KV
  // table renders the full string.
  if (c.started_at) bits.push(`started ${c.started_at.slice(0, 16)}`);
  if (c.finished_at) bits.push(`finished ${c.finished_at.slice(0, 16)}`);
  if (c.command) bits.push(`cmd: ${truncateText(c.command, 100)}`);
  const blockedSuffix = blocked
    ? `\nBLOCKED${blockerId ? ` by ${blockerId}` : ""}`
    : "";
  return `${title} (compute job)\n${bits.join(" · ")}${blockedSuffix}\n\n(click to open details)`;
}

function truncateText(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Subset of fields used to render decision-node affordances on the canvas.
 * `impact` = how many tasks auto-unblock when this decision flips to done
 * (see decisionImpactCount). `blocker` carries through so the label-builder
 * can light the LOUD halo when blocker == "operator-decision".  */
export interface DecisionMeta {
  impact: number;
  blocker: string | null;
}

/** Build the canvas tooltip for a decision-node. Short, one-line per fact;
 * the full Context / Decision / Consequences body lives in the per-task
 * adr.md, opened via the drawer. */
function composeDecisionTooltip(
  title: string,
  d: DecisionMeta,
  blocked: boolean,
  blockerId: string | null,
): string {
  const parts: string[] = [];
  if (d.impact > 0) {
    parts.push(`unblocks ${d.impact} task${d.impact === 1 ? "" : "s"} on resolve`);
  }
  if (d.blocker) parts.push(`blocker=${d.blocker}`);
  const blockedSuffix = blocked
    ? `\nBLOCKED${blockerId ? ` by ${blockerId}` : ""}`
    : "";
  const summary = parts.length ? `\n${parts.join(" · ")}` : "";
  return `${title} (decision)${summary}${blockedSuffix}\n\n(click to open details — full ADR body in adr.md)`;
}

/** Forward closure count from a decision-node — how many tasks would
 * auto-unblock when this decision's status flips to `done`. Operator's
 * leverage signal (lead a2a `554435df`): "decision that unblocks N tasks
 * = N-leverage; decide the high-N one first."
 *
 * Implementation: BFS over `depends_on` edges where source = the decision
 * node, recursively gathering reachable nodes. Restricted to NOT-YET-DONE
 * targets — a task that's already past this decision wouldn't count as
 * "unblocked by it." Self-loops + cycles are bounded by the visited set;
 * returns 0 for a decision with no dependents.
 *
 * Used to render the "⚖️ unblocks N" impact badge on every kind=decision
 * node label. Larger N = brighter chip on the board (high-leverage
 * decisions pop relative to single-task ones).
 */
export function decisionImpactCount(
  graph: GraphPayload,
  decisionId: string,
): number {
  const byId = new Map(graph.nodes.map((n) => [n.id, n] as const));
  const stack = [decisionId];
  const visited = new Set<string>([decisionId]);
  let count = 0;
  while (stack.length) {
    const cur = stack.pop()!;
    for (const e of graph.edges) {
      if (e.kind !== "depends_on" || e.source !== cur) continue;
      const target = byId.get(e.target);
      if (!target || visited.has(target.id)) continue;
      visited.add(target.id);
      // Only count rows that aren't already past this decision.
      if (target.status !== "done") count += 1;
      stack.push(target.id);
    }
  }
  return count;
}

/** Shorten a long blocker id for an inline node badge. Full id is preserved in
 * the hover tooltip + the Blockers section of the drawer. 16 chars keeps the
 * "← <id>" chip readable on the node without pushing the title off the row. */
function truncateId(id: string, max = 16): string {
  return id.length > max ? `${id.slice(0, max - 1)}…` : id;
}

/** First upstream blocker id for node X — used for the inline "← <id>" node
 * badge so the operator can see WHO is blocking X without opening the drawer.
 *
 * Order of precedence (matches the BlockersSection in NodeDetailPanel):
 *   1) explicit `blocks` edges into X (source = blocker, target = X)
 *   2) incoming `depends_on` deps whose status is not yet `done`
 * Returns null when X has no unresolved blocker (a status=blocked task with
 * NO blocker chain means a manual block — drawer is the right place for
 * detail; the node still gets the 🚧 prefix glyph). */
function firstBlockerFor(
  graph: GraphPayload,
  nodeId: string,
): string | null {
  const byId = new Map(graph.nodes.map((n) => [n.id, n] as const));
  const explicit = graph.edges.find(
    (e) => e.kind === "blocks" && e.target === nodeId,
  );
  if (explicit && byId.has(explicit.source)) return explicit.source;
  const dep = graph.edges.find(
    (e) =>
      e.kind === "depends_on" &&
      e.target === nodeId &&
      byId.get(e.source)?.status !== "done",
  );
  return dep ? dep.source : null;
}

/** Split nodes into the connected dependency graph vs the uncategorized pool.
 *
 * Scope-parameterized: operates only on nodes visible at the given drill-down
 * scope. Edges between two scope-visible nodes count for "connected"; edges
 * crossing the scope boundary are ignored (a child node connected ONLY to a
 * sibling at another level is treated as disconnected for THIS view).
 *
 * A node is "uncategorized" (belongs in the staging pool) when EITHER:
 *   - its note is exactly "uncategorized", OR
 *   - it has no dependency edges connecting it to the graph at this scope.
 */
export function partitionNodes(
  graph: GraphPayload,
  scope: string | null = null,
): {
  graphNodes: GraphNode[];
  poolNodes: GraphNode[];
} {
  const visible = scopeNodes(graph, scope);
  const visibleIds = new Set(visible.map((n) => n.id));

  const connected = new Set<string>();
  for (const e of graph.edges) {
    if (visibleIds.has(e.source) && visibleIds.has(e.target)) {
      connected.add(e.source);
      connected.add(e.target);
    }
  }

  const graphNodes: GraphNode[] = [];
  const poolNodes: GraphNode[] = [];
  for (const n of visible) {
    const taggedUncategorized = (n.note ?? "").trim() === "uncategorized";
    const disconnected = !connected.has(n.id);
    if (taggedUncategorized || disconnected) {
      poolNodes.push(n);
    } else {
      graphNodes.push(n);
    }
  }
  return { graphNodes, poolNodes };
}

export function buildFlow(
  graph: GraphPayload,
  scope: string | null = null,
): {
  nodes: Node[];
  edges: Edge[];
} {
  const { graphNodes } = partitionNodes(graph, scope);
  const inGraph = new Set(graphNodes.map((n) => n.id));

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 70 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of graphNodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of graph.edges) {
    if (
      e.kind === "depends_on" &&
      inGraph.has(e.source) &&
      inGraph.has(e.target)
    ) {
      g.setEdge(e.source, e.target);
    }
  }
  dagre.layout(g);

  const nodes: Node[] = graphNodes.map((n) => {
    const pos = g.node(n.id);
    const prio = n.priority != null ? ` · p${n.priority}` : "";
    // Parent-node drill-down AFFORDANCE: combine FIVE redundant signals so
    // the user knows BEFORE clicking that a parent will drill in (not open
    // the detail drawer). Operator UX feedback said the previous label-only
    // cues were too easy to miss; we now lean on a colored pill badge and a
    // distinct border SHAPE so the affordance reads at a glance:
    //   (1) a leading "⊞" expand glyph                  — universal "drill in"
    //   (2) a corner PILL BADGE "N ↓" via parentLabel  — concrete count, pop
    //   (3) a 5px DOUBLE border via parentNodeStyle    — different SHAPE
    //   (4) a stacked-card box-shadow pile             — depth = container
    //   (5) `zoom-in` hover cursor + tilt via CSS      — interaction cue
    // The label is now a ReactNode (was a string) so the badge can sit at
    // the corner via CSS — see board.css `.stx-cards-node__badge`.
    const kids = nodeChildCount(graph, n.id);
    const isParent = kids > 0;
    // Comment-count badge: a "💬N" suffix when the task has any comments, so
    // discussion is visible at a glance without opening the drawer.
    const ncomments = n.comments?.length ?? 0;
    const chat = ncomments > 0 ? `  💬${ncomments}` : "";
    const suffix = `${prio}${chat}`;
    // Blocked-task affordance (operator UX 2026-06-06): leading "🚧" glyph on
    // any task whose status is "blocked" so the board reads at a glance which
    // threads are stuck. We also embed an inline "← <id>" chip carrying the
    // FIRST blocker's id (lead-callout 2026-06-06 b9503957: "secret-migration-
    // phase3 ← blocked by ← ci-recovery-wave should jump out"). The full chain
    // is in the NodeDetailPanel's Blockers section when the drawer opens.
    const blocked = n.status === "blocked";
    const blockerId = blocked ? firstBlockerFor(graph, n.id) : null;
    // Compute-state affordance (north-star pillar #1, lead a2a 2c7a431d): a
    // leading "⚙" glyph + a richer tooltip on any row with `kind: "compute"`
    // so the operator can see at a glance which graph nodes are compute jobs
    // updated by an external writer (Spartan watcher / CI watcher etc., wired
    // by task #15) vs ordinary tasks the operator updates by hand.
    const compute: ComputeMeta | null =
      n.kind === "compute"
        ? {
            job_id: n.job_id,
            host: n.host,
            command: n.command,
            started_at: n.started_at,
            finished_at: n.finished_at,
          }
        : null;
    // Decision-node affordance (north-star pillar #4, lead a2a 4691b114 +
    // 554435df, operator TG 9524): a leading "⚖️" glyph + an "unblocks N"
    // impact-count badge on every `kind: "decision"` row. The impact count
    // (forward closure over depends_on) gives the operator the prioritization
    // signal — "this decision unblocks 7 tasks vs 1, so do this one first."
    const decision: DecisionMeta | null =
      n.kind === "decision"
        ? {
            impact: decisionImpactCount(graph, n.id),
            blocker: n.blocker,
          }
        : null;
    const label = isParent
      ? parentLabel(n.title, kids, suffix, blocked, blockerId, compute, decision)
      : leafLabel(n.title, suffix, blocked, blockerId, compute, decision);
    const base = nodeStyle(graph.status_colors[n.status]);
    return {
      id: n.id,
      position: {
        x: (pos?.x ?? 0) - NODE_W / 2,
        y: (pos?.y ?? 0) - NODE_H / 2,
      },
      data: { label },
      style: isParent
        ? parentNodeStyle(base, kids, graph.status_colors[n.status])
        : base,
      // Per-node className is forwarded by React Flow onto the wrapper DOM
      // element — used by board.css to set the hover cursor and tooltip
      // ("drill in" vs "details") and to scope a hover halo brighten.
      // Decision-nodes with blocker=operator-decision get an extra
      // `--decision-operator` modifier to drive the LOUD purple-gold halo
      // (this is the one the operator opens the UI to find — ADR-0003).
      className: [
        "stx-cards-node",
        isParent ? "stx-cards-node--parent" : "stx-cards-node--leaf",
        n.kind === "decision" ? "stx-cards-node--decision" : "",
        n.kind === "decision" && n.blocker === "operator-decision"
          ? "stx-cards-node--decision-operator"
          : "",
      ]
        .filter(Boolean)
        .join(" "),
      // `draggable: true` so dragging a node BODY moves it (drag-reorder →
      // onNodeDragStop persists priority); a per-node `false` would override
      // the root `nodesDraggable` and make a node-drag pan the canvas instead.
      // `connectable: true` routes drags from a node HANDLE to edge creation.
      draggable: true,
      connectable: true,
    };
  });

  // Lookup of node status by id so edges can detect "is the target currently
  // blocked?" without re-scanning the nodes list per edge.
  const statusById = new Map(graph.nodes.map((n) => [n.id, n.status] as const));

  const edges: Edge[] = graph.edges
    .filter((e) => inGraph.has(e.source) && inGraph.has(e.target))
    .map((e, i) => {
      const isBlock = e.kind === "blocks";
      // Edges INTO a status=blocked target are the live "this is what's
      // keeping you stuck" lines. Thicken + recolor them red so the blocker
      // chain jumps out on the canvas (lead 2026-06-06 b9503957: "bold/
      // colored the blocks + depends_on edges"). Non-blocked targets keep
      // their kind-default styling so the canvas doesn't become a sea of red.
      const targetBlocked = statusById.get(e.target) === "blocked";
      const stroke = targetBlocked
        ? EDGE_COLOR_BLOCKS
        : isBlock
        ? EDGE_COLOR_BLOCKS
        : EDGE_COLOR_DEPENDS;
      const strokeWidth = targetBlocked ? 3 : 2;
      // depends_on: default smoothstep edge with an arrowhead marker.
      // blocks:    custom `inhibition` edge (InhibitionEdge.tsx) — same body
      //            line as depends_on but with a perpendicular tee instead of
      //            an arrowhead, and NO text label (bar alone carries it).
      return {
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: isBlock ? INHIBITION_EDGE_TYPE : "smoothstep",
        // Carry the edge kind so the right-click "delete edge" handler knows
        // which task field (depends_on vs blocks) to scrub.
        data: { kind: e.kind },
        animated: false,
        style: { stroke, strokeWidth },
        markerEnd: isBlock
          ? undefined
          : {
              type: MarkerType.ArrowClosed,
              color: targetBlocked ? EDGE_COLOR_BLOCKS : EDGE_COLOR_DEPENDS,
            },
      };
    });

  return { nodes, edges };
}
