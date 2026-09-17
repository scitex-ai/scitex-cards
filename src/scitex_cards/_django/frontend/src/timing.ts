/** Client-side load timings for the Cards leaf: shell → data → interactive.
 *
 * WHY THIS EXISTS. The operator's standing complaint about this page is not a
 * missing feature, it is "slow first board" with no way to see WHERE the time
 * goes: the first paint, the /graph round-trip, and the moment the board is
 * actually usable are three different events, and until now the page reported
 * none of them. A duration you cannot attribute is a duration you can only
 * argue about.
 *
 * THREE MARKS, EACH ONE-SHOT (the span is the FIRST occurrence, not the last):
 *
 *   shell       — this module was evaluated, i.e. the bundle is running.
 *   data        — the first /graph payload was applied to the store.
 *   interactive — the board (any view) rendered for the first time.
 *
 * `performance.now()` is measured from navigation start, so `shell` is the
 * bundle's own load cost, and each later mark is a milestone on the same
 * timeline rather than a delta between marks. `performance.measure` is also
 * emitted (under `stx-cards:<name>`) so DevTools' Performance panel lines the
 * numbers up with the frames instead of leaving them as bare console output.
 *
 * The values are kept on `window.__stxCardsTimings` so a test, the header's
 * timing affordance and a human running one console snippet all read the SAME
 * numbers — a timing that only exists in a private closure is a claim, not a
 * measurement.
 */

export type LeafTimingName = "shell" | "data" | "interactive";

export interface LeafTiming {
  name: LeafTimingName;
  /** Milliseconds since navigation start. */
  ms: number;
}

declare global {
  interface Window {
    __stxCardsTimings?: LeafTiming[];
  }
}

const marks: LeafTiming[] = [];
const seen = new Set<LeafTimingName>();

/** Record a milestone once. Later calls for the same name are ignored. */
export function markTiming(name: LeafTimingName): void {
  if (seen.has(name)) return;
  seen.add(name);
  const ms = Math.round(performance.now());
  marks.push({ name, ms });
  if (typeof window !== "undefined") {
    window.__stxCardsTimings = marks;
  }
  try {
    performance.measure(`stx-cards:${name}`, { start: 0, end: ms });
  } catch {
    /* measure() is a DevTools affordance; never let it break the page. */
  }
}

/** Every milestone recorded so far, in order. */
export function readTimings(): LeafTiming[] {
  return marks.slice();
}

// The bundle is being evaluated right now: this IS the shell milestone.
markTiming("shell");
