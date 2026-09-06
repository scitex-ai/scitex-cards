/** Bottom-center toast with an optional Undo, driven by the store's `toast`.
 *
 * Shown after delete / group / edge mutations on the shared store so they're
 * reversible. Auto-dismisses after a few seconds; clicking Undo runs the
 * captured reversal and dismisses immediately.
 */

import { useEffect } from "react";
import { useBoardStore } from "./store/useBoardStore";

const TOAST_MS = 6000;

export function Toast() {
  const toast = useBoardStore((s) => s.toast);
  const dismissToast = useBoardStore((s) => s.dismissToast);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(dismissToast, TOAST_MS);
    return () => window.clearTimeout(id);
  }, [toast, dismissToast]);

  if (!toast) return null;

  return (
    <div className="stx-cards-toast" role="status" aria-live="polite">
      <span className="stx-cards-toast__msg">{toast.message}</span>
      {toast.undo && (
        <button
          type="button"
          className="stx-cards-toast__undo"
          onClick={() => {
            toast.undo?.();
            dismissToast();
          }}
        >
          Undo
        </button>
      )}
      <button
        type="button"
        className="stx-cards-toast__close"
        onClick={dismissToast}
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}
