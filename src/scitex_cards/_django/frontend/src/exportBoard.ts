/** Export the filtered/visible board to Markdown / CSV / JSON.
 *
 * Sister to clipboard.ts (per-card copy): this exports the whole set of
 * tasks visible under the current toolbar filter, as a single downloaded
 * file. Edges (depends_on / blocks) are reconstructed per task from
 * graph.edges so a paste/import elsewhere still carries relationships.
 */

import type { GraphNode, GraphPayload } from "./types/board";

function edgesFor(graph: GraphPayload, id: string) {
  const dependsOn: string[] = [];
  const blocks: string[] = [];
  for (const e of graph.edges) {
    if (e.kind === "depends_on" && e.target === id) dependsOn.push(e.source);
    if (e.kind === "blocks" && e.source === id) blocks.push(e.target);
  }
  return { dependsOn, blocks };
}

const STATUS_ORDER = [
  "goal",
  "in_progress",
  "blocked",
  "pending",
  "deferred",
  "failed",
  "done",
];

/** Markdown export: a section per status, with one bullet per task carrying
 * id + status + priority + repo + parent + deps + comment count. */
export function toMarkdown(graph: GraphPayload, nodes: GraphNode[]): string {
  const groups = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const list = groups.get(n.status) ?? [];
    list.push(n);
    groups.set(n.status, list);
  }
  const out: string[] = [
    `# scitex-cards — ${nodes.length} tasks`,
    `*store: \`${graph.store_path}\`*`,
    "",
  ];
  const status_order = [
    ...STATUS_ORDER.filter((s) => groups.has(s)),
    ...[...groups.keys()].filter((s) => !STATUS_ORDER.includes(s)).sort(),
  ];
  for (const s of status_order) {
    const items = groups.get(s) ?? [];
    out.push(`## ${s} (${items.length})`, "");
    for (const n of items) {
      const { dependsOn, blocks } = edgesFor(graph, n.id);
      const meta = [
        n.priority != null ? `p${n.priority}` : null,
        n.repo ? `repo:\`${n.repo}\`` : null,
        n.parent ? `parent:\`${n.parent}\`` : null,
        dependsOn.length ? `depends_on:[${dependsOn.join(",")}]` : null,
        blocks.length ? `blocks:[${blocks.join(",")}]` : null,
        n.comments?.length ? `💬${n.comments.length}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      out.push(`- **${n.title}** \`${n.id}\`${meta ? ` — ${meta}` : ""}`);
    }
    out.push("");
  }
  return out.join("\n");
}

function csvQuote(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/** CSV export with one row per task. */
export function toCsv(graph: GraphPayload, nodes: GraphNode[]): string {
  const header = [
    "id",
    "title",
    "status",
    "priority",
    "repo",
    "parent",
    "depends_on",
    "blocks",
    "comments",
  ];
  const rows = [header.join(",")];
  for (const n of nodes) {
    const { dependsOn, blocks } = edgesFor(graph, n.id);
    rows.push(
      [
        n.id,
        n.title,
        n.status,
        n.priority != null ? String(n.priority) : "",
        n.repo ?? "",
        n.parent ?? "",
        dependsOn.join("|"),
        blocks.join("|"),
        String(n.comments?.length ?? 0),
      ]
        .map(csvQuote)
        .join(","),
    );
  }
  return rows.join("\n");
}

/** JSON export: the full node payload + reconstructed edges per task. */
export function toJson(graph: GraphPayload, nodes: GraphNode[]): string {
  const tasks = nodes.map((n) => {
    const { dependsOn, blocks } = edgesFor(graph, n.id);
    return {
      ...n,
      depends_on: dependsOn,
      blocks,
    };
  });
  return JSON.stringify(
    { store_path: graph.store_path, count: tasks.length, tasks },
    null,
    2,
  );
}

/** Trigger a browser download of `text` as `filename`. */
export function downloadText(
  text: string,
  filename: string,
  mime: string,
): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
