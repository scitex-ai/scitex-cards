/** Global keyboard shortcuts for the board (renders nothing).
 *
 *   /            focus the search box
 *   n            new task
 *   e            edit the open/selected card
 *   Delete/Backspace  delete the selection (or the open card)
 *   Escape       clear the multi-selection / close menu + edge picker
 *
 * Ignored while typing in an input / textarea / select / contenteditable, so
 * these never hijack the search box, the editor, or a comment field.
 */

import { useEffect } from "react";
import { useBoardStore } from "./store/useBoardStore";

function isTyping(el: EventTarget | null): boolean {
  const node = el as HTMLElement | null;
  if (!node) return false;
  const tag = node.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    node.isContentEditable
  );
}

export function KeyboardShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useBoardStore.getState();

      // Escape works everywhere (incl. while typing — blurs then clears).
      if (e.key === "Escape") {
        if (s.menu) s.closeMenu();
        if (s.pendingEdge) s.closeEdgePicker();
        if (s.selectedIds.length) s.clearSelected();
        return;
      }
      if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;

      switch (e.key) {
        case "/": {
          e.preventDefault();
          const input = document.querySelector<HTMLInputElement>(
            ".stx-cards-toolbar__search",
          );
          input?.focus();
          input?.select();
          break;
        }
        case "n":
          e.preventDefault();
          s.beginCreate();
          break;
        case "e": {
          // Edit the drawer's task, else the lone multi-selected card.
          const id =
            s.selectedNodeId ??
            (s.selectedIds.length === 1 ? s.selectedIds[0] : null);
          if (id) {
            e.preventDefault();
            s.beginEdit(id);
          }
          break;
        }
        case "Delete":
        case "Backspace": {
          if (s.selectedIds.length > 0) {
            e.preventDefault();
            if (
              window.confirm(
                `Delete ${s.selectedIds.length} selected task(s)?`,
              )
            ) {
              void s.bulkDelete(s.selectedIds);
            }
          } else if (s.selectedNodeId && !s.editMode) {
            e.preventDefault();
            const node = s.graph?.nodes.find((n) => n.id === s.selectedNodeId);
            const label = node ? node.title : s.selectedNodeId;
            if (window.confirm(`Delete “${label}”?`)) {
              void s.deleteTask(s.selectedNodeId);
            }
          }
          break;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return null;
}
