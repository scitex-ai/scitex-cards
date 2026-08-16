/** Live auto-refresh for the shared store.
 *
 * The YAML task store is read/written by every agent, so the board can go
 * stale between actions. This polls a cheap `/rev` fingerprint (store mtime +
 * task count); when it changes:
 *   - if the user is NOT mid-interaction → silently reload the graph and flash
 *     a brief "synced" cue.
 *   - if the user IS mid-interaction (editing / creating / menu open / a write
 *     in flight) → don't clobber their work; raise a dismissible "Updated —
 *     refresh" pill they can click to reload when ready.
 *
 * Rendered once inside the board; renders only the transient indicator.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { useBoardStore } from "./store/useBoardStore";

const POLL_MS = 5000;

export function AutoRefresh() {
  const load = useBoardStore((s) => s.load);
  const refreshNow = useBoardStore((s) => s.refreshNow);
  const stale = useBoardStore((s) => s.stale);
  const setStale = useBoardStore((s) => s.setStale);
  const [flash, setFlash] = useState(false);
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let flashTimer: number | undefined;

    const tick = async () => {
      let rev: { mtime: number; count: number };
      try {
        rev = await api.rev();
      } catch {
        return; // transient; try again next tick
      }
      if (cancelled) return;

      const key = `${rev.mtime}:${rev.count}`;
      if (lastKey.current === null) {
        lastKey.current = key; // first sample — establish baseline
        return;
      }
      if (key === lastKey.current) return;

      // Read busy flags at fire time (not via subscription) so the poller
      // doesn't re-run on every store change.
      const s = useBoardStore.getState();
      const busy = s.editMode || s.creating || s.menu !== null || s.mutating;
      if (busy) {
        setStale(true); // offer, don't force — leave lastKey so we re-detect
        return;
      }
      lastKey.current = key;
      await load();
      if (cancelled) return;
      setFlash(true);
      flashTimer = window.setTimeout(() => setFlash(false), 1800);
    };

    const id = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      if (flashTimer) window.clearTimeout(flashTimer);
    };
  }, [load, setStale]);

  const onRefresh = () => {
    lastKey.current = null; // re-baseline after manual refresh
    void refreshNow();
  };

  if (stale) {
    return (
      <button
        type="button"
        className="stx-cards-sync stx-cards-sync--stale"
        onClick={onRefresh}
        title="The shared store changed — click to reload"
      >
        ● Updated — refresh
      </button>
    );
  }
  if (flash) {
    return (
      <div className="stx-cards-sync stx-cards-sync--ok" aria-live="polite">
        ✓ synced
      </div>
    );
  }
  return null;
}
