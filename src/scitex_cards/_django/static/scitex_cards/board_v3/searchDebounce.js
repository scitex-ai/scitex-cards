/* searchDebounce.js — when to rebuild the board while the operator types.
 *
 * Operator report (2026-07-30): after the 0.21.0 payload fix cut the graph
 * response 21.0 MB -> 11.9 MB, load felt better but "カーソルが間に合ってこない"
 * — the caret still could not keep up with the keyboard. That is a different
 * bottleneck from payload size, and the payload fix could never have addressed
 * it: it is INTERACTION cost, not LOAD cost.
 *
 * THE MECHANISM. `board_v3.html`'s search box called `render()` synchronously
 * from `oninput`. `render()` filters every node and rebuilds the whole layout,
 * and `oninput` is dispatched BEFORE the browser paints — so the rebuild sat on
 * the critical path of displaying the character just typed. The symptom is
 * therefore the caret lagging, not merely results lagging behind the caret;
 * those are different complaints and only one of them points here.
 *
 * THE POLICY, which is all this module owns: collapse a burst of keystrokes
 * into ONE trailing rebuild. Leading-edge would defeat the purpose (the first
 * keystroke of every burst would still pay full price), so this is
 * trailing-only.
 *
 * Pure by the board_v3 convention — timers only, no DOM, no STATE, no fetch —
 * so it is unit-testable under `node --test` like its sibling modules. The
 * handler that owns STATE and calls render() stays inline in the template.
 *
 * DELIBERATELY NOT DEBOUNCED BY THE CALLER: `renderSearchSuggest`. It populates
 * `_SEARCH_SUGG_ITEMS` and the suggestion list's `hidden` flag, which
 * `onSearchKeyDown` reads to decide what Enter / Tab / ArrowDown do. Deferring
 * it would make a fast typist's Enter fall through to the "no suggestions open"
 * branch — trading a working keyboard contract for a smaller saving.
 */

//: Wait this long after the LAST keystroke before rebuilding. 120ms sits under
//: the ~150ms that still reads as "instant" to a typist, while being long
//: enough to swallow a normal typing burst (~60-200ms between keys).
const SEARCH_RENDER_DEBOUNCE_MS = 120;

/** Create a trailing-edge debouncer.
 *
 * `delayMs` defaults to SEARCH_RENDER_DEBOUNCE_MS. `timers` is an injection
 * seam for tests (`{setTimeout, clearTimeout}`); it defaults to the globals, so
 * production callers pass nothing.
 *
 * Returns `{schedule, cancel, pending}`:
 *   schedule(fn) — run `fn` once, `delayMs` after the last schedule() call.
 *                  A later call REPLACES an earlier pending one, so a burst of
 *                  N keystrokes produces exactly one invocation.
 *   cancel()     — drop any pending run. Used when the work becomes moot
 *                  (the input cleared, the view switched away).
 *   pending()    — true while a run is queued. Exposed for tests and for a
 *                  caller that needs to flush before reading rendered DOM.
 */
function createSearchDebouncer(delayMs, timers) {
  const wait = typeof delayMs === "number" ? delayMs : SEARCH_RENDER_DEBOUNCE_MS;
  const _set = (timers && timers.setTimeout) || setTimeout;
  const _clear = (timers && timers.clearTimeout) || clearTimeout;
  let handle = null;

  function cancel() {
    if (handle !== null) {
      _clear(handle);
      handle = null;
    }
  }

  function schedule(fn) {
    cancel();
    handle = _set(() => {
      // Clear BEFORE running: if `fn` throws, a stale handle must not make
      // pending() lie forever, and the next schedule() must still work.
      handle = null;
      fn();
    }, wait);
  }

  function pending() {
    return handle !== null;
  }

  return { schedule, cancel, pending };
}

const _searchDebounceApi = {
  SEARCH_RENDER_DEBOUNCE_MS,
  createSearchDebouncer,
};

if (typeof globalThis !== "undefined") {
  globalThis.STX = globalThis.STX || {};
  globalThis.STX.searchDebounce = _searchDebounceApi;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = _searchDebounceApi;
}
