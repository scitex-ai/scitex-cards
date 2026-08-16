/** Edge-kind picker shown right after a node→node drag.
 *
 * React Flow's onConnect gives us the source/target but no edge semantics, so
 * instead of assuming `depends_on` we pop a tiny chooser at the drop point:
 *   →  depends-on (arrow)   — target depends on source
 *   ⊣  blocks (blocker)     — source blocks target
 * Picking one persists the edge; click-away / Escape cancels.
 */

import { useEffect } from "react";
import { EDGE_COLOR_BLOCKS, EDGE_COLOR_DEPENDS } from "./layout";
import { useBoardStore } from "./store/useBoardStore";

export function EdgeKindMenu() {
  const pendingEdge = useBoardStore((s) => s.pendingEdge);
  const closeEdgePicker = useBoardStore((s) => s.closeEdgePicker);
  const setEdge = useBoardStore((s) => s.setEdge);

  useEffect(() => {
    if (!pendingEdge) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeEdgePicker();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingEdge, closeEdgePicker]);

  if (!pendingEdge) return null;

  const { source, target, x, y } = pendingEdge;
  const choose = (kind: "depends_on" | "blocks") => {
    void setEdge("add", kind, source, target);
    closeEdgePicker();
  };

  const left = Math.min(x, window.innerWidth - 200);
  const top = Math.min(y, window.innerHeight - 120);

  return (
    <div
      className="stx-cards-menu__backdrop"
      onClick={closeEdgePicker}
      onContextMenu={(e) => {
        e.preventDefault();
        closeEdgePicker();
      }}
    >
      <div
        className="stx-cards-menu"
        style={{ left, top }}
        role="menu"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="stx-cards-menu__label">New edge</div>
        <button
          type="button"
          className="stx-cards-menu__item"
          role="menuitem"
          onClick={() => choose("depends_on")}
        >
          <span
            className="stx-cards-menu__edge-swatch"
            style={{ background: EDGE_COLOR_DEPENDS }}
            aria-hidden="true"
          />
          → depends-on
        </button>
        <button
          type="button"
          className="stx-cards-menu__item"
          role="menuitem"
          onClick={() => choose("blocks")}
        >
          <span
            className="stx-cards-menu__edge-swatch"
            style={{ background: EDGE_COLOR_BLOCKS }}
            aria-hidden="true"
          />
          ⊣ blocks
        </button>
      </div>
    </div>
  );
}
