/** Zustand store for the board: loaded graph payload + load / reorder /
 * select / drill actions.
 *
 * `reorderPriority` POSTs an ordered list of task ids to the backend
 * `/priority` endpoint (which assigns priority = 1..N in array order and
 * writes the YAML via `save_tasks`), then reloads the graph so the UI
 * reflects the new ordering from the canonical source of truth.
 *
 * `selectedNodeId` drives the right-side detail drawer (NodeDetailPanel) —
 * clicking a graph or pool LEAF card sets it; click-away / Escape / × clears
 * it. Held in the global store (not local component state) so a graph reload
 * after a successful drag-reorder preserves the open drawer.
 *
 * `drillPath` drives the nested-graph drill-down. Each entry is the id of a
 * parent node the user has descended INTO. Empty array = top-level view
 * (nodes whose `parent` is null/absent). Clicking a node with children
 * pushes its id; the breadcrumb sets the path to a prefix to navigate back.
 * Held in the store (not local component state) so the canvas keeps its
 * place across graph reloads (e.g. after a drag-reorder save).
 */

import { create } from "zustand";
import { api, type TaskInput } from "../api/client";
import { loadPersistedView, savePersistedView } from "../persist";
import { matchesSearchQuery, parseSearchQuery } from "../searchQuery";
import type { GraphPayload } from "../types/board";

// View state restored from localStorage / URL, used to seed the store below.
const _persisted = loadPersistedView();

/** Selectable task statuses, mirroring the backend VALID_STATUSES. Drives the
 * status dropdown in the editor and the "Set status ▸" context submenu. */
export const STATUSES = [
  "goal",
  "in_progress",
  "blocked",
  "done",
  "deferred",
  "failed",
  "cancelled",
] as const;

/** Right-click context-menu anchor. `taskId === null` = the canvas/pane menu
 * (offers "New task"); a non-null id = a card menu (edit / status / delete). */
export interface MenuState {
  x: number;
  y: number;
  taskId: string | null;
}

interface BoardStore {
  graph: GraphPayload | null;
  loading: boolean;
  /** True while a drag-reorder POST is in flight. Separate from `loading` so
   * the initial load spinner doesn't double-fire during interaction. */
  saving: boolean;
  error: string | null;
  /** Task id currently displayed in the detail drawer, or null if closed. */
  selectedNodeId: string | null;
  /** Stack of parent ids the user has drilled INTO (deepest at the end).
   * Empty = top-level scope. The currently-visible scope is the last entry
   * (`null` when empty). */
  drillPath: string[];
  /** Free-text filter (matches task title / id / repo, case-insensitive).
   * Empty string = no text filter. */
  query: string;
  /** Statuses to KEEP visible. Empty set = show all statuses. */
  activeStatuses: string[];
  /** Repos to KEEP visible. Empty set = show all repos. */
  activeRepos: string[];
  load: () => Promise<void>;
  reorderPriority: (order: string[]) => Promise<void>;
  selectNode: (id: string) => void;
  clearSelection: () => void;
  /** Descend into `parentId`'s child subgraph. Idempotent if already there. */
  drillInto: (parentId: string) => void;
  /** Pop back to the breadcrumb crumb at `depth` (0 = Home / top-level). */
  drillTo: (depth: number) => void;
  /** Replace the whole drill path (used by browser/mouse back-forward nav). */
  setDrillPath: (path: string[]) => void;
  /** Set the free-text filter. */
  setQuery: (q: string) => void;
  /** Toggle a status in/out of the keep-visible set. */
  toggleStatus: (status: string) => void;
  /** Toggle a repo in/out of the keep-visible set. */
  toggleRepo: (repo: string) => void;
  /** Replace the repo filter wholesale (used by the repo dropdown). */
  setRepos: (repos: string[]) => void;
  /** Clear the text query and the status + repo filters. */
  resetFilters: () => void;

  // ── CRUD ───────────────────────────────────────────────────────────────
  /** True while a create/update/delete POST is in flight (reuses `saving`'s
   * dim cue is enough; this is just for disabling the editor's Save button). */
  mutating: boolean;
  /** When true the detail drawer renders its editable form rather than the
   * read view. */
  editMode: boolean;
  /** When true the drawer is composing a brand-new task (no selected id). */
  creating: boolean;
  /** Open the drawer in edit mode for an existing task. */
  beginEdit: (id: string) => void;
  /** Open the drawer in create mode (blank draft). */
  beginCreate: () => void;
  /** Leave edit/create mode (back to read view, or close if was creating). */
  endEdit: () => void;
  /** Create a task, reload the graph, and open it in the drawer. */
  createTask: (input: TaskInput) => Promise<void>;
  /** Patch a task's fields and reload the graph. */
  updateTask: (id: string, input: TaskInput) => Promise<void>;
  /** Delete a task and reload the graph (closing the drawer if it was open). */
  deleteTask: (id: string) => Promise<void>;
  /** Append a comment to a task's thread and reload the graph. */
  addComment: (id: string, text: string, author?: string) => Promise<void>;
  /** Add/remove a dependency edge (source/target in graph orientation). */
  setEdge: (
    action: "add" | "remove",
    kind: "depends_on" | "blocks",
    source: string,
    target: string,
  ) => Promise<void>;

  // ── Edge-kind picker (after a node→node drag) ────────────────────────────
  /** A just-dragged connection awaiting a kind choice (arrow vs blocker), with
   * the drop-point viewport coords for positioning the picker. */
  pendingEdge: { source: string; target: string; x: number; y: number } | null;
  openEdgePicker: (
    source: string,
    target: string,
    x: number,
    y: number,
  ) => void;
  closeEdgePicker: () => void;

  // ── Right-click context menu ─────────────────────────────────────────────
  /** Active context menu, or null when closed. */
  menu: MenuState | null;
  /** Open the context menu at viewport (x, y) for a card (id) or canvas (null). */
  openMenu: (x: number, y: number, taskId: string | null) => void;
  /** Close the context menu. */
  closeMenu: () => void;

  // ── Live auto-refresh ────────────────────────────────────────────────────
  /** True when a newer revision of the shared store was detected while the
   * user was mid-interaction — a refresh is offered (pill) rather than forced. */
  stale: boolean;
  setStale: (v: boolean) => void;
  /** Reload the graph now and clear the stale flag. */
  refreshNow: () => Promise<void>;

  // ── View mode ────────────────────────────────────────────────────────────
  /** Which board view is active: the dependency graph, a flat table, or
   * the newest-first Recent triage surface (operator TG 513, 2026-06-12). */
  view: "graph" | "table" | "recent" | "calendar" | "timeline";
  setView: (v: "graph" | "table" | "recent" | "calendar" | "timeline") => void;

  // ── Multi-select (Ctrl+click) for bulk copy ──────────────────────────────
  /** Ids of cards marked via Ctrl/⌘+click; right-click → Copy acts on these. */
  selectedIds: string[];
  /** Toggle a card's membership in the multi-selection. */
  toggleSelected: (id: string) => void;
  /** Clear the multi-selection. */
  clearSelected: () => void;
  /** Set status on many tasks, then reload once + clear the selection. */
  bulkSetStatus: (ids: string[], status: string) => Promise<void>;
  /** Delete many tasks, then reload once + clear the selection. */
  bulkDelete: (ids: string[]) => Promise<void>;
  /** Nest the given children under `parentId`, then reload + clear selection. */
  bulkGroupUnder: (parentId: string, ids: string[]) => Promise<void>;

  // ── Undo toast ────────────────────────────────────────────────────────────
  /** Transient toast with an optional Undo action (delete / group / edge). */
  toast: { message: string; undo: (() => void) | null } | null;
  showToast: (message: string, undo?: (() => void) | null) => void;
  dismissToast: () => void;
}

export const useBoardStore = create<BoardStore>((set, get) => ({
  graph: null,
  loading: false,
  saving: false,
  error: null,
  selectedNodeId: null,
  drillPath: _persisted.drillPath,
  query: _persisted.query,
  activeStatuses: _persisted.activeStatuses,
  activeRepos: _persisted.activeRepos,
  mutating: false,
  editMode: false,
  creating: false,
  menu: null,
  pendingEdge: null,
  load: async () => {
    set({ loading: true, error: null });
    try {
      const graph = await api.graph();
      set({ graph, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },
  reorderPriority: async (order: string[]) => {
    set({ saving: true, error: null });
    try {
      await api.setPriorityOrder(order);
      // Re-fetch from the canonical store so any backend-side normalization
      // (skipped unknown ids, comment-preserved YAML, etc.) is reflected.
      const graph = await api.graph();
      set({ graph, saving: false });
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      // Reload so the UI snaps back to the on-disk state on failure rather
      // than displaying the optimistic (but not persisted) drag positions.
      void get().load();
    }
  },
  selectNode: (id: string) => set({ selectedNodeId: id }),
  clearSelection: () => set({ selectedNodeId: null }),
  drillInto: (parentId: string) =>
    set((s) => {
      // Idempotent: clicking the same already-current scope is a no-op so
      // double-clicks don't push a redundant crumb.
      if (s.drillPath[s.drillPath.length - 1] === parentId) return s;
      // Also clear any open detail drawer when changing scope so a stale
      // selection from the previous level doesn't linger.
      return {
        drillPath: [...s.drillPath, parentId],
        selectedNodeId: null,
      };
    }),
  drillTo: (depth: number) =>
    set((s) => {
      const clamped = Math.max(0, Math.min(depth, s.drillPath.length));
      if (clamped === s.drillPath.length) return s;
      return {
        drillPath: s.drillPath.slice(0, clamped),
        selectedNodeId: null,
      };
    }),
  setDrillPath: (path: string[]) =>
    set((s) =>
      // No-op if identical, so a popstate echo doesn't thrash the canvas.
      s.drillPath.length === path.length &&
      s.drillPath.every((v, i) => v === path[i])
        ? s
        : { drillPath: path, selectedNodeId: null },
    ),
  setQuery: (q: string) => set({ query: q }),
  toggleStatus: (status: string) =>
    set((s) => ({
      activeStatuses: s.activeStatuses.includes(status)
        ? s.activeStatuses.filter((x) => x !== status)
        : [...s.activeStatuses, status],
    })),
  toggleRepo: (repo: string) =>
    set((s) => ({
      activeRepos: s.activeRepos.includes(repo)
        ? s.activeRepos.filter((x) => x !== repo)
        : [...s.activeRepos, repo],
    })),
  setRepos: (repos: string[]) => set({ activeRepos: repos }),
  resetFilters: () => set({ query: "", activeStatuses: [], activeRepos: [] }),

  // ── CRUD ───────────────────────────────────────────────────────────────
  beginEdit: (id: string) =>
    set({ selectedNodeId: id, editMode: true, creating: false, menu: null }),
  beginCreate: () =>
    set({ selectedNodeId: null, editMode: true, creating: true, menu: null }),
  endEdit: () =>
    set((s) =>
      s.creating
        ? { editMode: false, creating: false, selectedNodeId: null }
        : { editMode: false },
    ),
  createTask: async (input: TaskInput) => {
    set({ mutating: true, error: null });
    try {
      const { task } = await api.createTask(input);
      const graph = await api.graph();
      // Open the freshly-created task in the read drawer so the user sees it.
      set({
        graph,
        mutating: false,
        editMode: false,
        creating: false,
        selectedNodeId: task.id,
      });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  updateTask: async (id: string, input: TaskInput) => {
    set({ mutating: true, error: null });
    try {
      await api.updateTask(id, input);
      const graph = await api.graph();
      set({ graph, mutating: false, editMode: false });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  deleteTask: async (id: string) => {
    set({ mutating: true, error: null });
    const title = get().graph?.nodes.find((n) => n.id === id)?.title ?? id;
    try {
      const res = await api.deleteTask(id);
      const graph = await api.graph();
      set((s) => ({
        graph,
        mutating: false,
        menu: null,
        // Close the drawer if it was showing the now-deleted task.
        selectedNodeId: s.selectedNodeId === id ? null : s.selectedNodeId,
        editMode: s.selectedNodeId === id ? false : s.editMode,
      }));
      get().showToast(`Deleted “${title}”`, async () => {
        await api.restoreTask(res.removed, res.refs);
        set({ graph: await api.graph() });
      });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },

  addComment: async (id: string, text: string, author?: string) => {
    set({ mutating: true, error: null });
    try {
      await api.addComment(id, text, author);
      const graph = await api.graph();
      set({ graph, mutating: false });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },

  setEdge: async (action, kind, source, target) => {
    set({ mutating: true, error: null });
    try {
      await api.edge(action, kind, source, target);
      const graph = await api.graph();
      set({ graph, mutating: false });
      if (action === "add") {
        const label = kind === "blocks" ? "blocks" : "depends-on";
        get().showToast(`Added ${label} edge`, async () => {
          await api.edge("remove", kind, source, target);
          set({ graph: await api.graph() });
        });
      }
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },

  // ── Edge-kind picker ─────────────────────────────────────────────────────
  openEdgePicker: (source, target, x, y) =>
    set({ pendingEdge: { source, target, x, y } }),
  closeEdgePicker: () => set({ pendingEdge: null }),

  // ── Right-click context menu ─────────────────────────────────────────────
  openMenu: (x: number, y: number, taskId: string | null) =>
    set({ menu: { x, y, taskId } }),
  closeMenu: () => set({ menu: null }),

  // ── Live auto-refresh ────────────────────────────────────────────────────
  stale: false,
  setStale: (v: boolean) => set({ stale: v }),
  refreshNow: async () => {
    set({ stale: false });
    await get().load();
  },

  // ── View mode ────────────────────────────────────────────────────────────
  view: _persisted.view,
  setView: (v: "graph" | "table" | "recent" | "calendar" | "timeline") =>
    set({ view: v }),

  // ── Multi-select ─────────────────────────────────────────────────────────
  selectedIds: [],
  toggleSelected: (id: string) =>
    set((s) => ({
      selectedIds: s.selectedIds.includes(id)
        ? s.selectedIds.filter((x) => x !== id)
        : [...s.selectedIds, id],
    })),
  clearSelected: () => set({ selectedIds: [] }),
  bulkSetStatus: async (ids: string[], status: string) => {
    set({ mutating: true, error: null });
    try {
      for (const id of ids) await api.updateTask(id, { status });
      const graph = await api.graph();
      set({ graph, mutating: false, selectedIds: [], menu: null });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  bulkDelete: async (ids: string[]) => {
    set({ mutating: true, error: null });
    try {
      const removed: {
        task: Record<string, unknown>;
        refs: { id: string; field: string }[];
      }[] = [];
      for (const id of ids) {
        const res = await api.deleteTask(id);
        removed.push({ task: res.removed, refs: res.refs });
      }
      const graph = await api.graph();
      set((s) => ({
        graph,
        mutating: false,
        selectedIds: [],
        menu: null,
        selectedNodeId: ids.includes(s.selectedNodeId ?? "")
          ? null
          : s.selectedNodeId,
      }));
      get().showToast(`Deleted ${ids.length} tasks`, async () => {
        for (const r of removed) await api.restoreTask(r.task, r.refs);
        set({ graph: await api.graph() });
      });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  bulkGroupUnder: async (parentId: string, ids: string[]) => {
    set({ mutating: true, error: null });
    try {
      const byId = new Map((get().graph?.nodes ?? []).map((n) => [n.id, n]));
      const prev: { id: string; parent: string | null }[] = [];
      for (const id of ids) {
        if (id !== parentId) {
          prev.push({ id, parent: byId.get(id)?.parent ?? null });
          await api.updateTask(id, { parent: parentId });
        }
      }
      const graph = await api.graph();
      set({ graph, mutating: false, selectedIds: [], menu: null });
      get().showToast(`Grouped ${prev.length} under task`, async () => {
        for (const p of prev) await api.updateTask(p.id, { parent: p.parent });
        set({ graph: await api.graph() });
      });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },

  // ── Undo toast ────────────────────────────────────────────────────────────
  toast: null,
  showToast: (message: string, undo: (() => void) | null = null) =>
    set({ toast: { message, undo } }),
  dismissToast: () => set({ toast: null }),
}));

// Persist the view slice (filter / scope / mode) whenever it changes, so a
// reload — or a copied ?q=/?scope= link — reopens where the user left off.
useBoardStore.subscribe((s) =>
  savePersistedView({
    query: s.query,
    activeStatuses: s.activeStatuses,
    activeRepos: s.activeRepos,
    view: s.view,
    drillPath: s.drillPath,
  }),
);

/** True iff a task matches the current text query + status filter.
 *
 * Empty query matches everything; empty status set matches every status.
 * Text match is case-insensitive across title / id / repo. Shared by the
 * graph (dim non-matches) and the pool (hide non-matches) so the toolbar
 * filters both views consistently.
 *
 * When the query contains a GitHub-style qualifier (project:foo,
 * status:blocked, …) it delegates to ``searchQuery.matchesSearchQuery``
 * — the same parser shipped to the board_v3 (operator-live) template
 * via static/scitex_cards/board_v3/searchQuery.js. Operator TG 12315/16
 * (2026-06-12): the operator typed ``project: paper-scitex-clew`` into
 * the search bar already expecting GitHub-style behaviour. Pure free-
 * text queries (no `<key>:` at all) fall through to the original case-
 * insensitive substring path so PR #80's behaviour is unchanged for the
 * common case.
 */
export function taskMatchesFilter(
  task: {
    id: string;
    title: string;
    status: string;
    repo?: string | null;
    note?: string | null;
    comments?: { text: string }[];
    project?: string | null;
    agent?: string | null;
    assignee?: string | null;
    scope?: string | null;
    kind?: string | null;
    host?: string | null;
    parent?: string | null;
    priority?: number | null;
  },
  query: string,
  activeStatuses: string[],
  activeRepos: string[] = [],
): boolean {
  if (activeStatuses.length > 0 && !activeStatuses.includes(task.status)) {
    return false;
  }
  if (activeRepos.length > 0 && !activeRepos.includes(task.repo ?? "")) {
    return false;
  }
  const raw = query.trim();
  if (!raw) return true;
  // Qualifier-syntax path: when the raw query contains a `<key>:` token
  // we hand the whole thing to the parser. Bare-text matching there
  // reuses the fzf-style subsequence haystack (title/note/id/etc.).
  if (/[A-Za-z_][A-Za-z0-9_-]*:/.test(raw)) {
    const parsed = parseSearchQuery(raw);
    return matchesSearchQuery(task, parsed);
  }
  // Legacy fast path — preserved verbatim so PR #80's behaviour is intact.
  // Deep search: title / id / repo + note body + comment text.
  const q = raw.toLowerCase();
  const comments = (task.comments ?? []).map((c) => c.text).join(" ");
  const hay =
    `${task.title} ${task.id} ${task.repo ?? ""} ${task.note ?? ""} ${comments}`.toLowerCase();
  return hay.includes(q);
}
