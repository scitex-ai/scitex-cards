/** View-state persistence: keep the user's filter / scope / view across
 * reloads (localStorage) and reflect the query + current scope in the URL so a
 * copied link reopens the same view.
 *
 * Only the *view* is persisted — never task data (that's the shared store).
 * The theme is owned by the scitex-ui shell (`<html data-theme>`), so it is
 * deliberately not persisted here.
 */

const KEY = "stx-cards-view";

export interface PersistedView {
  query: string;
  activeStatuses: string[];
  activeRepos: string[];
  view: "graph" | "table" | "recent" | "calendar" | "timeline";
  drillPath: string[];
}

const DEFAULTS: PersistedView = {
  query: "",
  activeStatuses: [],
  activeRepos: [],
  view: "graph",
  drillPath: [],
};

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

/** Initial view: localStorage, then overridden by any `?q=` / `?scope=` URL
 * params (so shared links win). Always returns a well-formed object. */
export function loadPersistedView(): PersistedView {
  const out: PersistedView = { ...DEFAULTS };
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<PersistedView>;
      out.query = typeof p.query === "string" ? p.query : "";
      out.activeStatuses = strings(p.activeStatuses);
      out.activeRepos = strings(p.activeRepos);
      out.view =
        p.view === "table"
          ? "table"
          : p.view === "recent"
            ? "recent"
            : p.view === "calendar"
              ? "calendar"
              : p.view === "timeline"
                ? "timeline"
                : "graph";
      out.drillPath = strings(p.drillPath);
    }
  } catch {
    /* corrupt / unavailable storage — fall back to defaults */
  }
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    if (q !== null) out.query = q;
    const scope = params.get("scope");
    if (scope) out.drillPath = [scope]; // single-level scope from a link
  } catch {
    /* no window (SSR) */
  }
  return out;
}

/** Persist the view to localStorage and mirror query + deepest scope into the
 * URL (replaceState — no history spam). */
export function savePersistedView(v: PersistedView): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    /* quota / unavailable — non-fatal */
  }
  try {
    const params = new URLSearchParams(window.location.search);
    if (v.query) params.set("q", v.query);
    else params.delete("q");
    const scope = v.drillPath.length ? v.drillPath[v.drillPath.length - 1] : "";
    if (scope) params.set("scope", scope);
    else params.delete("scope");
    const qs = params.toString();
    const url = qs
      ? `${window.location.pathname}?${qs}`
      : window.location.pathname;
    // Preserve the current history state object — the drill-history nav
    // (DrillHistory) keeps its scope stack there; passing null would wipe it
    // and break mouse/browser back-forward.
    window.history.replaceState(window.history.state, "", url);
  } catch {
    /* ignore */
  }
}
