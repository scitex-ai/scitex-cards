/** Right-side detail drawer for a single task.
 *
 * Three modes, driven by the board store:
 *   - read   — renders the task's `note` as markdown + metadata (default).
 *   - edit   — an editable form for an existing task (store.editMode).
 *   - create — the same form with a blank draft (store.creating).
 *
 * Opened when a graph or pool node is clicked (read), or via the right-click
 * menu's "Edit…" / "New task" (edit / create). Close behaviour: × button,
 * click on the dimmed backdrop, or Escape.
 *
 * The drawer is a sibling of the React Flow canvas (rendered by `GraphView`)
 * and is absolutely positioned so it overlays the board without affecting
 * layout. Pool clicks reuse the same drawer.
 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { TaskInput } from "./api/client";
import { ChatPanel } from "./ChatPanel";
import { STATUSES, useBoardStore } from "./store/useBoardStore";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";

function StatusBadge({
  status,
  color,
}: {
  status: string;
  color: StatusColor | undefined;
}) {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  return (
    <span
      className="stx-cards-detail__badge"
      style={{
        background: c.fill,
        border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
        color: "#222",
      }}
    >
      {status}
    </span>
  );
}

/** Editable form shared by edit + create modes. */
function DetailEditor({
  node,
  graph,
  creating,
  onCancel,
}: {
  node: GraphNode | null;
  graph: GraphPayload;
  creating: boolean;
  onCancel: () => void;
}) {
  const createTask = useBoardStore((s) => s.createTask);
  const updateTask = useBoardStore((s) => s.updateTask);
  const mutating = useBoardStore((s) => s.mutating);

  const [title, setTitle] = useState(node?.title ?? "");
  const [status, setStatus] = useState(node?.status ?? "pending");
  const [priority, setPriority] = useState(
    node?.priority != null ? String(node.priority) : "",
  );
  const [repo, setRepo] = useState(node?.repo ?? "");
  const [parent, setParent] = useState(node?.parent ?? "");
  const [note, setNote] = useState(
    node && node.note && node.note !== "uncategorized" ? node.note : "",
  );

  const parentOptions = graph.nodes.filter((n) => n.id !== node?.id);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    const prioNum = priority.trim() === "" ? null : Number(priority);
    const input: TaskInput = {
      title: title.trim(),
      status,
      priority: Number.isFinite(prioNum as number) ? prioNum : null,
      repo: repo.trim(),
      parent: parent || null,
      note,
    };
    if (creating) void createTask(input);
    else if (node) void updateTask(node.id, input);
  };

  return (
    <form className="stx-cards-detail__form" onSubmit={onSubmit}>
      <label className="stx-cards-field">
        <span>Title</span>
        <input
          className="stx-cards-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
          required
        />
      </label>

      <div className="stx-cards-field-row">
        <label className="stx-cards-field">
          <span>Status</span>
          <select
            className="stx-cards-input"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="stx-cards-field stx-cards-field--narrow">
          <span>Priority</span>
          <input
            className="stx-cards-input"
            type="number"
            value={priority}
            placeholder="—"
            onChange={(e) => setPriority(e.target.value)}
          />
        </label>
      </div>

      <label className="stx-cards-field">
        <span>Repo</span>
        <input
          className="stx-cards-input"
          value={repo}
          placeholder="optional"
          onChange={(e) => setRepo(e.target.value)}
        />
      </label>

      <label className="stx-cards-field">
        <span>Parent</span>
        <select
          className="stx-cards-input"
          value={parent}
          onChange={(e) => setParent(e.target.value)}
        >
          <option value="">— none (top level) —</option>
          {parentOptions.map((n) => (
            <option key={n.id} value={n.id}>
              {n.title}
            </option>
          ))}
        </select>
      </label>

      <label className="stx-cards-field stx-cards-field--grow">
        <span>Note (markdown)</span>
        <textarea
          className="stx-cards-input stx-cards-textarea"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={8}
        />
      </label>

      <div className="stx-cards-detail__actions">
        <button
          type="button"
          className="stx-cards-btn"
          onClick={onCancel}
          disabled={mutating}
        >
          Cancel
        </button>
        <button
          type="submit"
          className="stx-cards-btn stx-cards-btn--primary"
          disabled={mutating || !title.trim()}
        >
          {mutating ? "Saving…" : creating ? "Create" : "Save"}
        </button>
      </div>
    </form>
  );
}

/** localStorage key remembering the last author typed, so a commenter
 * doesn't re-enter their name each time. */
const AUTHOR_KEY = "stx-cards-comment-author";

function fmtTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Append-only comment thread for a task: existing comments (oldest first)
 * plus an add box. The author defaults to the remembered value; leaving it
 * blank lets the backend stamp $USER. */
function CommentsSection({ node }: { node: GraphNode }) {
  const addComment = useBoardStore((s) => s.addComment);
  const mutating = useBoardStore((s) => s.mutating);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState(
    () => localStorage.getItem(AUTHOR_KEY) ?? "",
  );
  const comments = node.comments ?? [];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    if (author.trim()) localStorage.setItem(AUTHOR_KEY, author.trim());
    void addComment(node.id, body, author.trim() || undefined);
    setText("");
  };

  return (
    <section className="stx-cards-comments">
      <h3 className="stx-cards-comments__title">
        Comments {comments.length > 0 && `(${comments.length})`}
      </h3>
      {comments.length === 0 ? (
        <p className="stx-cards-comments__empty">
          <em>No comments yet.</em>
        </p>
      ) : (
        <ul className="stx-cards-comments__list">
          {comments.map((c, i) => (
            <li className="stx-cards-comment" key={`${c.ts}-${i}`}>
              <div className="stx-cards-comment__meta">
                <span className="stx-cards-comment__author">{c.author}</span>
                <span className="stx-cards-comment__ts">{fmtTs(c.ts)}</span>
              </div>
              <div className="stx-cards-comment__text">{c.text}</div>
            </li>
          ))}
        </ul>
      )}
      <form className="stx-cards-comments__form" onSubmit={submit}>
        <input
          className="stx-cards-input stx-cards-comments__author"
          value={author}
          placeholder="your name (optional)"
          onChange={(e) => setAuthor(e.target.value)}
          aria-label="Comment author"
        />
        <textarea
          className="stx-cards-input stx-cards-comments__text"
          value={text}
          placeholder="Add a comment…"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          aria-label="Comment text"
        />
        <div className="stx-cards-comments__actions">
          <button
            type="submit"
            className="stx-cards-btn stx-cards-btn--primary"
            disabled={mutating || !text.trim()}
          >
            {mutating ? "…" : "Comment"}
          </button>
        </div>
      </form>
    </section>
  );
}

/** "What is blocking this task?" + "What does this task block?" section.
 *
 * Operator UX 2026-06-06: "ブロッカーが何かわからないので、card にブロッカー
 * 可視化". For any selected task X this section enumerates two graph slices
 * read directly from the loaded edges + nodes:
 *
 *   - **Blockers** — concrete things keeping X stuck. Composition:
 *     (a) `depends_on` deps that are NOT yet `done` (incoming depends_on
 *         edges where target=X; X needs the source to finish first).
 *     (b) explicit `blocks` edges into X (source IS keeping X stopped).
 *     Each row is clickable → switches the drawer to that node, so the
 *     operator can chase the chain without losing their place.
 *
 *   - **Blocks (downstream)** — things X is currently keeping stuck. Mirror
 *     of (a) + (b): tasks that depend_on X and tasks X explicitly blocks.
 *
 * "done" deps are listed separately under a `<details>` toggle ("Satisfied
 * deps") so they don't clutter the live-blocker view but the trail is still
 * inspectable. When everything is empty we render a single explicit "Nothing
 * blocking this — and nothing waiting on it." line so absence is informative
 * rather than the section silently disappearing.
 */
function BlockersSection({
  node,
  graph,
}: {
  node: GraphNode;
  graph: GraphPayload;
}) {
  const selectNode = useBoardStore((s) => s.selectNode);

  // Index nodes by id once per render so each lookup is O(1).
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  // Upstream blockers — incoming depends_on (source = dep, target = X) where
  // the dep is not yet `done`, PLUS incoming `blocks` edges (source = blocker,
  // target = X). "done" deps are not blockers — they're satisfied prereqs.
  const upstreamDeps = graph.edges
    .filter((e) => e.kind === "depends_on" && e.target === node.id)
    .map((e) => byId.get(e.source))
    .filter((n): n is GraphNode => n != null);
  const explicitBlockers = graph.edges
    .filter((e) => e.kind === "blocks" && e.target === node.id)
    .map((e) => byId.get(e.source))
    .filter((n): n is GraphNode => n != null);
  const blockers: { node: GraphNode; why: string }[] = [
    ...upstreamDeps
      .filter((n) => n.status !== "done")
      .map((n) => ({ node: n, why: "depends_on (not done yet)" })),
    ...explicitBlockers.map((n) => ({
      node: n,
      why: "blocks (explicit)",
    })),
  ];
  const satisfiedDeps = upstreamDeps.filter((n) => n.status === "done");

  // Downstream — things waiting on X.
  const dependents = graph.edges
    .filter((e) => e.kind === "depends_on" && e.source === node.id)
    .map((e) => byId.get(e.target))
    .filter((n): n is GraphNode => n != null);
  const blocksTargets = graph.edges
    .filter((e) => e.kind === "blocks" && e.source === node.id)
    .map((e) => byId.get(e.target))
    .filter((n): n is GraphNode => n != null);
  const downstream: { node: GraphNode; why: string }[] = [
    ...dependents.map((n) => ({ node: n, why: "depends on this" })),
    ...blocksTargets.map((n) => ({
      node: n,
      why: "explicitly blocked by this",
    })),
  ];

  const empty = blockers.length === 0 && downstream.length === 0;

  const renderRow = (n: GraphNode, why: string, key: string) => {
    const c = graph.status_colors[n.status];
    return (
      <li className="stx-cards-blocker" key={key}>
        <button
          type="button"
          className="stx-cards-blocker__link"
          onClick={() => selectNode(n.id)}
          title={`Open details for ${n.title}`}
        >
          <StatusBadge status={n.status} color={c} />
          <span className="stx-cards-blocker__title">{n.title}</span>
          <span className="stx-cards-blocker__why">{why}</span>
        </button>
      </li>
    );
  };

  return (
    <section className="stx-cards-blockers">
      <h3 className="stx-cards-blockers__title">
        🚧 Blockers {blockers.length > 0 && `(${blockers.length})`}
      </h3>
      {empty && (
        <p className="stx-cards-blockers__empty">
          <em>Nothing blocking this — and nothing waiting on it.</em>
        </p>
      )}
      {blockers.length > 0 ? (
        <ul className="stx-cards-blockers__list">
          {blockers.map((b, i) =>
            renderRow(b.node, b.why, `b-${b.node.id}-${i}`),
          )}
        </ul>
      ) : !empty ? (
        <p className="stx-cards-blockers__hint">
          <em>Nothing is currently blocking this task.</em>
        </p>
      ) : null}

      {satisfiedDeps.length > 0 && (
        <details className="stx-cards-blockers__satisfied">
          <summary>✓ Satisfied deps ({satisfiedDeps.length})</summary>
          <ul className="stx-cards-blockers__list">
            {satisfiedDeps.map((n, i) =>
              renderRow(n, "depends_on (done)", `s-${n.id}-${i}`),
            )}
          </ul>
        </details>
      )}

      {downstream.length > 0 && (
        <>
          <h4 className="stx-cards-blockers__subtitle">
            Blocks (downstream) — {downstream.length}
          </h4>
          <ul className="stx-cards-blockers__list">
            {downstream.map((d, i) =>
              renderRow(d.node, d.why, `d-${d.node.id}-${i}`),
            )}
          </ul>
        </>
      )}
    </section>
  );
}

/** KV table for the compute-job metadata on a `kind: "compute"` row.
 *
 * Lead a2a `2c7a431d` directive: "opening a compute card answers 'what is
 * this and where does it run' immediately." Renders the validated compute
 * fields (job_id / host / command / started_at / finished_at) as a small
 * two-column table at the top of the drawer body, before the Blockers
 * section. Rows with a null/empty value are omitted so the table stays
 * compact for in-flight jobs that don't have a finished_at yet.
 *
 * The `command` row is special — long shell pipelines are common, so it
 * gets its own full-width row below the compact KV grid and wraps as
 * pre-wrap text. The canvas tooltip shows the same command truncated to
 * ~100 chars; here we show the full string.
 */
function ComputeMetaSection({ node }: { node: GraphNode }) {
  if (node.kind !== "compute") return null;

  const rows: Array<{ label: string; value: string }> = [];
  if (node.host) rows.push({ label: "host", value: node.host });
  if (node.job_id) rows.push({ label: "job_id", value: node.job_id });
  if (node.started_at)
    rows.push({ label: "started_at", value: node.started_at });
  if (node.finished_at)
    rows.push({ label: "finished_at", value: node.finished_at });

  return (
    <section className="stx-cards-compute" aria-label="Compute job metadata">
      <h3 className="stx-cards-compute__title">⚙ Compute job</h3>
      {rows.length > 0 && (
        <dl className="stx-cards-compute__kv">
          {rows.map((r) => (
            <div className="stx-cards-compute__row" key={r.label}>
              <dt className="stx-cards-compute__key">{r.label}</dt>
              <dd className="stx-cards-compute__val">
                <code>{r.value}</code>
              </dd>
            </div>
          ))}
        </dl>
      )}
      {node.command && (
        <div className="stx-cards-compute__cmd">
          <div className="stx-cards-compute__cmd-label">command</div>
          <pre className="stx-cards-compute__cmd-text">
            <code>{node.command}</code>
          </pre>
        </div>
      )}
      {rows.length === 0 && !node.command && (
        <p className="stx-cards-compute__empty">
          <em>
            No compute metadata recorded yet (writer hasn't populated this row).
          </em>
        </p>
      )}
    </section>
  );
}

function DetailReader({
  node,
  graph,
}: {
  node: GraphNode;
  graph: GraphPayload;
}) {
  const note = (node.note ?? "").trim();
  const hasNote = note.length > 0 && note !== "uncategorized";
  return (
    <div className="stx-cards-detail__body">
      <ComputeMetaSection node={node} />
      <BlockersSection node={node} graph={graph} />
      {hasNote ? (
        <div className="stx-cards-detail__markdown">
          <ReactMarkdown>{note}</ReactMarkdown>
        </div>
      ) : (
        <p className="stx-cards-detail__empty">
          <em>No note yet for this task.</em>
        </p>
      )}
      <CommentsSection node={node} />
      {/* Phase-6 CHAT surface — lead a2a `74db4f2d` + `10afa799`.
          Operator↔agent thread sitting on top of the same per-card
          `comments[]` substrate the CommentsSection above reads; adds
          30s auto-poll + write-back via /chat/<id>. The two surfaces
          render the same data; the chat one is the "live conversation"
          intent (polling + bubble layout + author colors). */}
      <ChatPanel cardId={node.id} />
    </div>
  );
}

export function NodeDetailPanel({
  node,
  color,
  editMode,
  creating,
  onClose,
  onEdit,
  graph,
}: {
  node: GraphNode | null;
  color: StatusColor | undefined;
  editMode: boolean;
  creating: boolean;
  onClose: () => void;
  onEdit: () => void;
  graph: GraphPayload;
}) {
  const endEdit = useBoardStore((s) => s.endEdit);

  // Close on Escape — but only in read mode; in edit/create Escape should
  // back out of the form (handled by the editor's own surface), not nuke
  // unsaved input. Here we close the whole drawer only when reading.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !editMode) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editMode]);

  const title = creating ? "New task" : (node?.title ?? "");

  return (
    <div
      className="stx-cards-detail__backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={creating ? "Create task" : `Task detail: ${title}`}
      onClick={onClose}
    >
      <aside className="stx-cards-detail" onClick={(e) => e.stopPropagation()}>
        <header className="stx-cards-detail__header">
          <div className="stx-cards-detail__title-row">
            <h2 className="stx-cards-detail__title">{title || "Untitled"}</h2>
            <div className="stx-cards-detail__title-actions">
              {!editMode && node && (
                <button
                  type="button"
                  className="stx-cards-detail__edit"
                  onClick={onEdit}
                  aria-label="Edit task"
                  title="Edit"
                >
                  ✎
                </button>
              )}
              <button
                type="button"
                className="stx-cards-detail__close"
                onClick={onClose}
                aria-label="Close"
              >
                ×
              </button>
            </div>
          </div>
          {!editMode && node && (
            <div className="stx-cards-detail__meta">
              <StatusBadge status={node.status} color={color} />
              {node.priority != null && (
                <span className="stx-cards-detail__prio">
                  priority {node.priority}
                </span>
              )}
              {node.repo && (
                <span className="stx-cards-detail__repo">
                  <code>{node.repo}</code>
                </span>
              )}
              <span className="stx-cards-detail__id">
                id: <code>{node.id}</code>
              </span>
            </div>
          )}
        </header>
        {editMode ? (
          <DetailEditor
            node={node}
            graph={graph}
            creating={creating}
            onCancel={endEdit}
          />
        ) : node ? (
          <DetailReader node={node} graph={graph} />
        ) : null}
      </aside>
    </div>
  );
}

/** Hook wrapper: resolve drawer state from the board store. Renders nothing
 * unless a node is selected (read/edit) or we're composing a new task. */
export function NodeDetailPanelContainer() {
  const graph = useBoardStore((s) => s.graph);
  const selectedNodeId = useBoardStore((s) => s.selectedNodeId);
  const editMode = useBoardStore((s) => s.editMode);
  const creating = useBoardStore((s) => s.creating);
  const clearSelection = useBoardStore((s) => s.clearSelection);
  const endEdit = useBoardStore((s) => s.endEdit);
  const beginEdit = useBoardStore((s) => s.beginEdit);

  if (!graph) return null;
  const open = creating || selectedNodeId != null;
  if (!open) return null;

  const node = selectedNodeId
    ? (graph.nodes.find((n) => n.id === selectedNodeId) ?? null)
    : null;
  // A selected id that no longer resolves (deleted out from under us) and not
  // creating -> nothing to show.
  if (!creating && !node) return null;

  const onClose = () => {
    if (editMode) endEdit();
    clearSelection();
  };

  return (
    <NodeDetailPanel
      node={node}
      color={node ? graph.status_colors[node.status] : undefined}
      editMode={editMode}
      creating={creating}
      graph={graph}
      onClose={onClose}
      onEdit={() => node && beginEdit(node.id)}
    />
  );
}
