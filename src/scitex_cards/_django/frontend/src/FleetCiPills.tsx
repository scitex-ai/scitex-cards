/** Fleet CI-status pills strip — the operator's "is the fleet red right
 * now?" at-a-glance answer.
 *
 * Renders one pill per watched repo (config-driven, NO hardcoded slugs).
 * Polls ``/fleet/ci-status`` every 30 seconds; on per-repo errors the
 * pill shows a red ``!`` icon and surfaces the adapter message via
 * ``title``. When the config has no repos OR the endpoint 500s, the
 * strip hides itself with a tiny footnote so an unconfigured operator
 * sees a single low-key hint, not a noisy red block.
 *
 * The component is intentionally self-contained — the back-end shape
 * (see ``handlers/fleet/ci_status_view.py``) is the only contract.
 */

import { useEffect, useState } from "react";

/** Status families known to the back-end ``_overall_from_checks`` reducer. */
export type CiOverall = "success" | "failure" | "pending" | "unknown";

/** One repo's CI summary as returned by ``/fleet/ci-status``. */
export interface CiRepoOk {
  slug: string;
  branch: string;
  head_sha: string;
  overall: CiOverall;
  checks: { name: string; status: string; conclusion: string }[];
}

/** Per-repo adapter error — surfaces the back-end message verbatim. */
export interface CiRepoErr {
  slug: string;
  error: string;
}

export type CiRepo = CiRepoOk | CiRepoErr;

export interface CiPayload {
  repos: CiRepo[];
  config: { repos: string[] };
}

/** Discriminator. Keeping this outside the component makes it trivially
 * testable by node — see ``tests/.../test_fleet_ci_pills.py``. */
export function isCiRepoErr(repo: CiRepo): repo is CiRepoErr {
  return Object.prototype.hasOwnProperty.call(repo, "error");
}

/** Pure mapping from overall-status -> the CSS modifier suffix.
 *
 * Kept as a pure exported function so the contract test can pin the
 * mapping for each known status without rendering. The CSS file owns
 * the visual rendering (color tokens, ring, etc.); this only emits the
 * suffix that selects the right rule. */
export function pillModifier(repo: CiRepo): string {
  if (isCiRepoErr(repo)) return "error";
  switch (repo.overall) {
    case "success":
      return "success";
    case "failure":
      return "failure";
    case "pending":
      return "pending";
    default:
      return "unknown";
  }
}

/** Build the tooltip text for one pill — surfaces enough to be useful
 * without a click-through. For errors: the adapter message. For OK
 * repos: branch, short SHA, conclusion summary. */
export function pillTooltip(repo: CiRepo): string {
  if (isCiRepoErr(repo)) {
    return `${repo.slug}: adapter error — ${repo.error}`;
  }
  const sha = repo.head_sha ? repo.head_sha.slice(0, 7) : "(no sha)";
  return `${repo.slug} @ ${repo.branch} (${sha}) — ${repo.overall}`;
}

const POLL_MS = 30_000;
const ENDPOINT = "/fleet/ci-status";

interface State {
  payload: CiPayload | null;
  /** Set when the endpoint itself fails (network down, 500 from a
   * malformed config). The strip hides with a single-line footnote. */
  globalError: string | null;
}

/** Top-level component — mounted by ``TodoBoard.tsx`` inside the
 * STATUS group of the toolbar. */
export function FleetCiPills() {
  const [state, setState] = useState<State>({
    payload: null,
    globalError: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch(ENDPOINT, { credentials: "same-origin" });
        if (!res.ok) {
          // The view returns 500 only when config-load itself fails;
          // per-repo errors come back inside a 200. Show a footnote
          // rather than a noisy banner.
          const text = await res.text().catch(() => "");
          if (cancelled) return;
          setState({
            payload: null,
            globalError: `HTTP ${res.status}${text ? `: ${text.slice(0, 200)}` : ""}`,
          });
          return;
        }
        const data: CiPayload = await res.json();
        if (cancelled) return;
        setState({ payload: data, globalError: null });
      } catch (err) {
        if (cancelled) return;
        setState({
          payload: null,
          globalError: err instanceof Error ? err.message : String(err),
        });
      }
    }

    void tick();
    const handle = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  if (state.globalError) {
    return (
      <span
        className="stx-todo-fleet-ci stx-todo-fleet-ci--note"
        title={state.globalError}
      >
        no CI status configured
      </span>
    );
  }
  if (!state.payload) {
    // Initial load — don't flash empty content; render an invisible
    // placeholder so the toolbar grid doesn't reflow on first paint.
    return (
      <span
        className="stx-todo-fleet-ci stx-todo-fleet-ci--loading"
        aria-hidden="true"
      />
    );
  }
  const { repos } = state.payload;
  if (repos.length === 0) {
    // Config absent / empty — the spec's graceful-hide path.
    return (
      <span
        className="stx-todo-fleet-ci stx-todo-fleet-ci--note"
        title={
          "Set fleet.ci_status.repos in ~/.scitex/cards/dashboard.json " +
          "or SCITEX_TODO_FLEET_CI_REPOS=owner/name,owner/other to enable."
        }
      >
        no CI status configured
      </span>
    );
  }

  return (
    <span
      className="stx-todo-fleet-ci"
      role="group"
      aria-label="Fleet CI status"
    >
      {repos.map((repo) => {
        const modifier = pillModifier(repo);
        const tooltip = pillTooltip(repo);
        const isErr = isCiRepoErr(repo);
        // Use the bare repo name (after the last "/") to keep the pill
        // compact — the full slug lives in the tooltip.
        const display = repo.slug.includes("/")
          ? repo.slug.split("/").slice(-1)[0]
          : repo.slug;
        return (
          <span
            key={repo.slug}
            className={`stx-todo-fleet-ci__pill stx-todo-fleet-ci__pill--${modifier}`}
            title={tooltip}
          >
            <span className="stx-todo-fleet-ci__name">{display}</span>
            <span className="stx-todo-fleet-ci__dot" aria-hidden="true">
              {isErr ? "!" : "●"}
            </span>
          </span>
        );
      })}
    </span>
  );
}

export default FleetCiPills;
