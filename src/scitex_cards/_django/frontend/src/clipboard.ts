/** Build a copy-to-clipboard text block for one or more tasks.
 *
 * Includes the content (title, note) AND metadata — id, status, priority,
 * repo, parent, dependency edges, comment count, and the store FILE PATH —
 * so a pasted card is self-describing. Multiple tasks are separated by `---`.
 */

import type { GraphPayload } from "./types/board";

function depsOf(graph: GraphPayload, id: string) {
  const dependsOn: string[] = [];
  const blocks: string[] = [];
  for (const e of graph.edges) {
    if (e.kind === "depends_on" && e.target === id) dependsOn.push(e.source);
    if (e.kind === "blocks" && e.source === id) blocks.push(e.target);
  }
  return { dependsOn, blocks };
}

function formatOne(graph: GraphPayload, id: string): string | null {
  const n = graph.nodes.find((x) => x.id === id);
  if (!n) return null;
  const { dependsOn, blocks } = depsOf(graph, id);
  const lines = [
    `# ${n.title}`,
    `id: ${n.id}`,
    `status: ${n.status}`,
    `priority: ${n.priority ?? "-"}`,
    `repo: ${n.repo ?? "-"}`,
    `parent: ${n.parent ?? "-"}`,
    `depends_on: ${dependsOn.length ? dependsOn.join(", ") : "-"}`,
    `blocks: ${blocks.length ? blocks.join(", ") : "-"}`,
    // store_path is internal: omit the line entirely on a public deployment
    // (the server omits the key there) rather than exporting "null".
    ...(graph.store_path ? [`file: ${graph.store_path}`] : []),
  ];
  const note = (n.note ?? "").trim();
  if (note && note !== "uncategorized") {
    lines.push("", note);
  }
  const comments = n.comments ?? [];
  if (comments.length) {
    lines.push("", "comments:");
    for (const c of comments) {
      lines.push(`- ${c.ts} ${c.author}: ${c.text}`);
    }
  }
  return lines.join("\n");
}

/** Render the given task ids as a single clipboard string (skips unknown ids). */
export function formatTasksForCopy(
  graph: GraphPayload,
  ids: string[],
): string {
  return ids
    .map((id) => formatOne(graph, id))
    .filter((s): s is string => s !== null)
    .join("\n\n---\n\n");
}

/** Copy the given tasks to the clipboard. Returns the number copied (0 on
 * failure or no clipboard access). */
export async function copyTasks(
  graph: GraphPayload,
  ids: string[],
): Promise<number> {
  const text = formatTasksForCopy(graph, ids);
  if (!text) return 0;
  try {
    await navigator.clipboard.writeText(text);
    return ids.length;
  } catch {
    // Fallback for non-secure contexts: a hidden textarea + execCommand.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      return ids.length;
    } catch {
      return 0;
    }
  }
}
