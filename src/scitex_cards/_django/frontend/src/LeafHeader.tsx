/** The Cards leaf's canonical identity band: title, version, project, timings.
 *
 * WHY A BAND AND NOT THE EXISTING HEADER. This page had no leaf identity of its
 * own: its header opened with a "Board" region chip and the sentence "SciTeX
 * Card — dependency graph", which names the VIEW, not the app, and it carried
 * no version anywhere. In a Hub workspace, where several leaf apps are mounted
 * into one shell, that makes the page unidentifiable — the operator's report
 * was literally "no Cards leaf title/version/project picker".
 *
 * THE FOUR FACTS, and where each one comes from — none of them is invented
 * here:
 *
 *   title     — "SciTeX Cards", the product name. Matches the heading the
 *               server-rendered pages (`_page_header.html`) already use, so the
 *               two page families cannot name the product differently.
 *   version   — `#app-mount[data-app-version]`, rendered by `standalone.html`
 *               from the same `_version` the Django pages print. The bundle
 *               ships no version literal of its own: a hard-coded number here
 *               would be a second source that goes stale on the next release
 *               (the exact failure mode that keeps `_version` authoritative).
 *               Absent attribute = no chip, never a guess.
 *   project   — the `<select>` is bound to the SAME filter state the toolbar's
 *               repo dropdown uses (`activeRepos` / `setRepos`), so the picker
 *               filters both views identically instead of being a second,
 *               half-wired control that only looks like it works.
 *   timings   — `shell` / `data` / `interactive` ms from `./timing`, read on
 *               the frame after the board's first paint. The operator's "slow
 *               first board" was unarguable because nothing on the page said
 *               which of the three was slow.
 */

import { useEffect, useMemo, useState } from "react";

import { readTimings, type LeafTiming } from "./timing";
import { useBoardStore } from "./store/useBoardStore";
import type { GraphPayload } from "./types/board";

/** The mount element's server-rendered app version, or "" when it is absent. */
function appVersion(): string {
  if (typeof document === "undefined") return "";
  const mount = document.getElementById("app-mount");
  const version = mount?.dataset.appVersion?.trim() ?? "";
  return version === "?" ? "" : version;
}

export function LeafHeader({ graph }: { graph?: GraphPayload | null }) {
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const setRepos = useBoardStore((s) => s.setRepos);
  const [timings, setTimings] = useState<LeafTiming[]>(() => readTimings());

  // The version is a server fact, so read it once per mount rather than per
  // render — it cannot change while the bundle is running.
  const version = useMemo(appVersion, []);

  // Projects come from the payload, not from a registry: a project with no
  // cards is not a project this picker can usefully filter to, and a registry
  // read would be a second source of truth to keep in sync.
  const projects = useMemo(() => {
    const seen = new Set<string>();
    for (const node of graph?.nodes ?? []) {
      const repo = (node.repo ?? "").trim();
      if (repo) seen.add(repo);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [graph]);

  // `interactive` is marked after the board's first paint, so re-read on the
  // next frame; without this the band would report two of its three numbers.
  useEffect(() => {
    const id = window.requestAnimationFrame(() => setTimings(readTimings()));
    return () => window.cancelAnimationFrame(id);
  }, [graph]);

  const selected = activeRepos[0] ?? "";

  return (
    <header className="stx-cards-leaf">
      <span className="stx-cards-leaf__title">SciTeX Cards</span>
      {version && <span className="stx-cards-leaf__ver">v{version}</span>}
      <label className="stx-cards-leaf__picker">
        <span className="stx-cards-leaf__picker-label">Project</span>
        <select
          className={`stx-cards-leaf__project${
            selected ? " stx-cards-leaf__project--on" : ""
          }`}
          value={selected}
          onChange={(e) => setRepos(e.target.value ? [e.target.value] : [])}
          aria-label="Project"
          title="Filter the board to one project"
          disabled={projects.length === 0}
        >
          <option value="">
            {projects.length ? `All projects (${projects.length})` : "No projects in this store"}
          </option>
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      {timings.length > 0 && (
        <span
          className="stx-cards-leaf__timings"
          title="Milliseconds since navigation start: bundle evaluated, first /graph applied, board painted"
        >
          {timings.map((t) => `${t.name} ${t.ms}ms`).join(" · ")}
        </span>
      )}
    </header>
  );
}
