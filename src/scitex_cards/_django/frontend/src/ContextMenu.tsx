/** Right-click context menu for the board.
 *
 * A single menu instance driven by the store's `menu` state. Opened by:
 *   - right-click on a graph node  (GraphView onNodeContextMenu)
 *   - right-click on a pool card   (UncategorizedPool item onContextMenu)
 *   - right-click on empty canvas  (GraphView onPaneContextMenu)
 *
 * For a card it offers Edit… / Set status / Delete; for the canvas it offers
 * New task. Closes on action, click-away, or Escape.
 */

import { useEffect, useRef } from "react";
import { copyTasks } from "./clipboard";
import { STATUSES, useBoardStore } from "./store/useBoardStore";

export function ContextMenu() {
  const menu = useBoardStore((s) => s.menu);
  const closeMenu = useBoardStore((s) => s.closeMenu);
  const beginEdit = useBoardStore((s) => s.beginEdit);
  const beginCreate = useBoardStore((s) => s.beginCreate);
  const updateTask = useBoardStore((s) => s.updateTask);
  const deleteTask = useBoardStore((s) => s.deleteTask);
  const graph = useBoardStore((s) => s.graph);
  const selectedIds = useBoardStore((s) => s.selectedIds);
  const clearSelected = useBoardStore((s) => s.clearSelected);
  const bulkSetStatus = useBoardStore((s) => s.bulkSetStatus);
  const bulkDelete = useBoardStore((s) => s.bulkDelete);
  const bulkGroupUnder = useBoardStore((s) => s.bulkGroupUnder);
  const showToast = useBoardStore((s) => s.showToast);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMenu();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu, closeMenu]);

  if (!menu) return null;

  const task = menu.taskId
    ? graph?.nodes.find((n) => n.id === menu.taskId)
    : null;

  // Bulk mode: the right-clicked card is part of a 2+ multi-selection, so
  // Set status / Delete / Group act on the whole selection.
  const bulk =
    !!menu.taskId &&
    selectedIds.length > 1 &&
    selectedIds.includes(menu.taskId);

  // Clamp so the menu never overflows the right/bottom edge.
  const MENU_W = 200;
  const MENU_H = 320;
  const left = Math.min(menu.x, window.innerWidth - MENU_W - 8);
  const top = Math.min(menu.y, window.innerHeight - MENU_H - 8);

  return (
    <div
      className="stx-cards-menu__backdrop"
      onClick={closeMenu}
      onContextMenu={(e) => {
        e.preventDefault();
        closeMenu();
      }}
    >
      <div
        ref={ref}
        className="stx-cards-menu"
        style={{ left, top }}
        role="menu"
        onClick={(e) => e.stopPropagation()}
      >
        {menu.taskId === null ? (
          <button
            type="button"
            className="stx-cards-menu__item"
            role="menuitem"
            onClick={beginCreate}
          >
            ✚ New task
          </button>
        ) : (
          <>
            <div className="stx-cards-menu__label" title={menu.taskId}>
              {task ? task.title : menu.taskId}
            </div>
            <button
              type="button"
              className="stx-cards-menu__item"
              role="menuitem"
              onClick={() => {
                // Copy the multi-selection if the clicked card is part of it
                // (and there's more than one); otherwise just this card.
                const id = menu.taskId as string;
                const ids =
                  selectedIds.length > 1 && selectedIds.includes(id)
                    ? selectedIds
                    : [id];
                if (graph) void copyTasks(graph, ids);
                clearSelected();
                closeMenu();
              }}
            >
              📋 Copy
              {selectedIds.length > 1 && selectedIds.includes(menu.taskId)
                ? ` ${selectedIds.length} selected`
                : ""}
            </button>
            <button
              type="button"
              className="stx-cards-menu__item"
              role="menuitem"
              onClick={() => beginEdit(menu.taskId as string)}
            >
              ✎ Edit…
            </button>
            {bulk && (
              <button
                type="button"
                className="stx-cards-menu__item"
                role="menuitem"
                onClick={() =>
                  void bulkGroupUnder(menu.taskId as string, selectedIds)
                }
              >
                ▤ Group {selectedIds.length - 1} under this
              </button>
            )}
            <div className="stx-cards-menu__sep" />
            <div className="stx-cards-menu__group-label">
              {bulk ? `Set status (${selectedIds.length})` : "Set status"}
            </div>
            <div className="stx-cards-menu__statuses">
              {STATUSES.map((s) => {
                const c = graph?.status_colors[s];
                const current = !bulk && task?.status === s;
                return (
                  <button
                    type="button"
                    key={s}
                    className={`stx-cards-menu__status${
                      current ? " stx-cards-menu__status--current" : ""
                    }`}
                    role="menuitem"
                    onClick={() =>
                      bulk
                        ? void bulkSetStatus(selectedIds, s)
                        : void updateTask(menu.taskId as string, { status: s })
                    }
                  >
                    <span
                      className="stx-cards-menu__swatch"
                      style={{
                        background: c?.fill ?? "#888",
                        borderColor: c?.stroke ?? "#888",
                      }}
                      aria-hidden="true"
                    />
                    {s}
                  </button>
                );
              })}
            </div>
            {!bulk && task?.parent && (
              <>
                <div className="stx-cards-menu__sep" />
                <button
                  type="button"
                  className="stx-cards-menu__item"
                  role="menuitem"
                  onClick={() => {
                    const id = menu.taskId as string;
                    const prev = task.parent ?? null;
                    void updateTask(id, { parent: null });
                    showToast("Moved to top level", () => {
                      void updateTask(id, { parent: prev });
                    });
                  }}
                >
                  ▤ Move to top level
                </button>
              </>
            )}
            <div className="stx-cards-menu__sep" />
            <button
              type="button"
              className="stx-cards-menu__item stx-cards-menu__item--danger"
              role="menuitem"
              onClick={() => {
                if (bulk) {
                  if (
                    window.confirm(
                      `Delete ${selectedIds.length} tasks? This cannot be undone.`,
                    )
                  ) {
                    void bulkDelete(selectedIds);
                  } else {
                    closeMenu();
                  }
                  return;
                }
                const label = task ? task.title : (menu.taskId as string);
                if (window.confirm(`Delete "${label}"? This cannot be undone.`)) {
                  void deleteTask(menu.taskId as string);
                } else {
                  closeMenu();
                }
              }}
            >
              🗑 Delete{bulk ? ` ${selectedIds.length}` : ""}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
