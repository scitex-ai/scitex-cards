/** Fleet timing-chart panel — Phase 5 surface of the FLEET DASHBOARD.
 *
 * Operator's intent (TG, relayed by lead a2a ``74db4f2d`` + ``10afa799``,
 * 2026-06-14): "record what took how long → self-improvement". This panel
 * is the chart layer over the Phase-4 ``/fleet/timing`` endpoint — it
 * shows the operator "what's taking how long across the fleet" so the
 * fleet can see + fix its own bottlenecks.
 *
 * Visual contract:
 *   - Collapsed default: a small ``📊 timing`` pill in the toolbar's
 *     STATUS group next to ``FleetMeshPanel``. Click to expand.
 *   - Expanded: a compact toolbar with WINDOW (7d / 30d / 90d) +
 *     GROUP-BY (Agent / Project / Group) controls, an inline SVG bar
 *     chart with one row per group-by key, and a diagnostic footer.
 *   - Each row shows label + median bar + p95 bar for
 *     ``started_to_done_s``; bar color escalates green → warn → error
 *     as the bar grows (operator's "slow" signal).
 *   - Rows sort by ``p95_started_to_done_s`` descending so the
 *     bottleneck rides at the TOP.
 *
 * Polls ``/fleet/timing?window_days=<state>`` every 60s — timing
 * changes slowly, no need for the 30s cadence of the live mesh / hosts
 * surfaces.
 *
 * On adapter error the panel renders ``📊 (no timing data)`` + a red
 * ``!`` marker and surfaces the back-end error in ``title`` — fail-loud
 * principle.
 *
 * NO hardcoded proper nouns — every agent / project / group label
 * comes from the back-end registry response. NO hardcoded colors —
 * bar shade picks one of three design-token CSS classes
 * (``--status-success`` / ``--status-warning`` / ``--status-error``).
 *
 * CARD(phase-5.b): per-task drill-down — clicking a bar surfaces the
 * constituent task IDs. Phase-4 backend doesn't ship that today.
 * CARD(phase-5.b): CDF / histogram overlays — Phase-4 emits median + p95
 * only.
 * CARD(phase-5.b): sparkline / time-series overlay — Phase-4 is an
 * aggregate snapshot.
 * CARD(phase-5.b): a2a-turn-duration surfacing — Phase 4.b backend gap.
 */

import { useEffect, useMemo, useState } from "react";

/** Per-row stats for one agent / project / group. Mirrors the Phase-4
 * back-end shape. */
export interface TimingRow {
  n_tasks_done: number;
  median_started_to_done_s: number | null;
  p95_started_to_done_s: number | null;
  median_created_to_started_s: number | null;
}

/** Successful timing payload — mirrors the Phase-4 back-end shape. */
export interface TimingPayloadOk {
  window_days: number;
  window_start: string;
  window_end: string;
  per_agent: Record<string, TimingRow>;
  per_project: Record<string, TimingRow>;
  per_group: Record<string, TimingRow>;
  n_tasks_in_window: number;
  n_tasks_missing_timestamps: number;
}

/** Adapter-failure payload shape (HTTP 500 body). */
export interface TimingPayloadErr {
  error: string;
}

export type TimingPayload = TimingPayloadOk | TimingPayloadErr;

/** Discriminator — kept outside the component for trivial node-side
 * testing in lock-step with the FleetMeshPanel pattern. */
export function isTimingPayloadErr(p: TimingPayload): p is TimingPayloadErr {
  return Object.prototype.hasOwnProperty.call(p, "error");
}

export type GroupBy = "agent" | "project" | "group";
export type WindowDays = 7 | 30 | 90;

/** Format a duration in seconds as a short human-readable string.
 *
 * Examples:
 *   1.5    → "1.5s"
 *   45     → "45s"
 *   90     → "1m 30s"
 *   3600   → "1h"
 *   7200   → "2h"
 *   12_345 → "3.4h"
 *
 * Exported so the contract test pins the formatter without a TS
 * rebuild. ``null`` returns the dash literal ``"—"``.
 */
export function formatDurationSeconds(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (!Number.isFinite(seconds)) return "—";
  const s = Math.max(0, seconds);
  if (s < 1) {
    // sub-second: keep one decimal so the operator sees the millisec
    return `${s.toFixed(1)}s`;
  }
  if (s < 60) {
    if (s < 10 && s % 1 !== 0) return `${s.toFixed(1)}s`;
    return `${Math.round(s)}s`;
  }
  if (s < 3600) {
    const mins = Math.floor(s / 60);
    const rem = Math.round(s - mins * 60);
    if (rem === 0) return `${mins}m`;
    return `${mins}m ${rem}s`;
  }
  if (s < 86400) {
    const hours = s / 3600;
    if (Number.isInteger(hours)) return `${hours}h`;
    return `${hours.toFixed(1)}h`;
  }
  const days = s / 86400;
  if (Number.isInteger(days)) return `${days}d`;
  return `${days.toFixed(1)}d`;
}

/** Compute a bar's width-percentage given the value and the chart's
 * max. Returns a value in ``[0, 100]``; ``null`` → 0; max ≤ 0 → 0.
 *
 * Exported so the contract test pins the math.
 */
export function barWidthPct(value: number | null, max: number): number {
  if (value === null || value === undefined) return 0;
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (!Number.isFinite(max) || max <= 0) return 0;
  const pct = (value / max) * 100;
  if (pct < 0) return 0;
  if (pct > 100) return 100;
  return pct;
}

/** Map a bar's width-percentage to one of three design-token CSS
 * classes (green / warn / error). The thresholds are visual only — the
 * raw duration is still in the tooltip.
 *
 * The SINGLE point where bar magnitude becomes a visual token; the
 * actual color value lives in ``fleet-timing.css``.
 */
export function barColorToken(pct: number): string {
  if (!Number.isFinite(pct) || pct <= 0) {
    return "stx-cards-fleet-timing__bar--ok";
  }
  if (pct < 50) return "stx-cards-fleet-timing__bar--ok";
  if (pct < 80) return "stx-cards-fleet-timing__bar--warn";
  return "stx-cards-fleet-timing__bar--slow";
}

/** Sort a ``per_*`` row map's keys by p95 descending (slowest first
 * — operator-friendly bottleneck-at-top ordering). Rows with a null
 * p95 sink to the bottom. Stable tie-break on key name (alphabetical).
 *
 * Exported so the contract test pins the order.
 */
export function sortKeysByP95Desc(rows: Record<string, TimingRow>): string[] {
  const entries = Object.entries(rows);
  entries.sort((a, b) => {
    const pa = a[1].p95_started_to_done_s;
    const pb = b[1].p95_started_to_done_s;
    // nulls last
    if (pa === null && pb === null) return a[0].localeCompare(b[0]);
    if (pa === null) return 1;
    if (pb === null) return -1;
    if (pb !== pa) return pb - pa;
    return a[0].localeCompare(b[0]);
  });
  return entries.map(([k]) => k);
}

/** Compact panel label: ``📊 timing · <N> rows``. The number of rows is
 * the count of buckets for the current group-by dimension. */
export function timingPanelLabel(p: TimingPayloadOk, groupBy: GroupBy): string {
  const rows = pickRows(p, groupBy);
  const n = Object.keys(rows).length;
  return `📊 timing · ${n} rows`;
}

/** Pick the right ``per_*`` map for the current group-by selection. */
export function pickRows(
  p: TimingPayloadOk,
  groupBy: GroupBy,
): Record<string, TimingRow> {
  if (groupBy === "agent") return p.per_agent || {};
  if (groupBy === "project") return p.per_project || {};
  return p.per_group || {};
}

const POLL_MS = 60_000;
const ENDPOINT = "/fleet/timing";

interface State {
  payload: TimingPayloadOk | null;
  adapterError: string | null;
  expanded: boolean;
  windowDays: WindowDays;
  groupBy: GroupBy;
}

/** Top-level component — mounted by ``CardsBoard.tsx`` next to the
 * ``FleetMeshPanel`` in the board header's STATUS group. */
export function FleetTimingPanel() {
  const [state, setState] = useState<State>({
    payload: null,
    adapterError: null,
    expanded: false,
    windowDays: 30,
    groupBy: "agent",
  });

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const url = `${ENDPOINT}?window_days=${state.windowDays}`;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) {
          let errBody = `HTTP ${res.status}`;
          try {
            const data = (await res.json()) as TimingPayloadErr;
            if (typeof data?.error === "string" && data.error.length > 0) {
              errBody = data.error;
            }
          } catch {
            /* fall through to the HTTP-status default */
          }
          if (cancelled) return;
          setState((prev) => ({
            ...prev,
            payload: null,
            adapterError: errBody,
          }));
          return;
        }
        const data: TimingPayloadOk = await res.json();
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          payload: data,
          adapterError: null,
        }));
      } catch (err) {
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          payload: null,
          adapterError: err instanceof Error ? err.message : String(err),
        }));
      }
    }

    void tick();
    const handle = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
    // Re-fetch when the operator changes the window (fresh data,
    // different aggregate). groupBy is purely a render-side switch,
    // no need to re-fetch.
  }, [state.windowDays]);

  // Compute the sorted-row-key list + the chart-scale max once per
  // payload + group-by change.
  const chart = useMemo(() => {
    if (!state.payload) {
      return {
        keys: [] as string[],
        max: 0,
        rows: {} as Record<string, TimingRow>,
      };
    }
    const rows = pickRows(state.payload, state.groupBy);
    const keys = sortKeysByP95Desc(rows);
    let max = 0;
    for (const k of keys) {
      const r = rows[k];
      if (r.p95_started_to_done_s !== null && r.p95_started_to_done_s > max) {
        max = r.p95_started_to_done_s;
      }
      if (
        r.median_started_to_done_s !== null &&
        r.median_started_to_done_s > max
      ) {
        max = r.median_started_to_done_s;
      }
    }
    return { keys, max, rows };
  }, [state.payload, state.groupBy]);

  if (state.adapterError) {
    return (
      <span
        className="stx-cards-fleet-timing stx-cards-fleet-timing--error"
        title={state.adapterError}
        role="group"
        aria-label="Fleet timing — adapter error"
      >
        <span className="stx-cards-fleet-timing__label">
          📊 (no timing data)
        </span>
        <span className="stx-cards-fleet-timing__dot" aria-hidden="true">
          !
        </span>
      </span>
    );
  }

  if (!state.payload) {
    return (
      <span
        className="stx-cards-fleet-timing stx-cards-fleet-timing--loading"
        aria-hidden="true"
      />
    );
  }

  const payload = state.payload;

  if (!state.expanded) {
    return (
      <button
        type="button"
        className="stx-cards-fleet-timing stx-cards-fleet-timing--collapsed"
        onClick={() => setState((prev) => ({ ...prev, expanded: true }))}
        title={`click to expand · ${payload.n_tasks_in_window} tasks in window · ${payload.n_tasks_missing_timestamps} missing timestamps`}
        aria-label="Open fleet timing chart"
      >
        <span className="stx-cards-fleet-timing__label">
          {timingPanelLabel(payload, state.groupBy)}
        </span>
      </button>
    );
  }

  return (
    <span
      className="stx-cards-fleet-timing stx-cards-fleet-timing--expanded"
      role="group"
      aria-label="Fleet timing chart"
    >
      <span className="stx-cards-fleet-timing__header">
        <span className="stx-cards-fleet-timing__label">
          {timingPanelLabel(payload, state.groupBy)}
        </span>
        <label className="stx-cards-fleet-timing__control">
          window
          <select
            className="stx-cards-fleet-timing__select"
            value={state.windowDays}
            onChange={(e) =>
              setState((prev) => ({
                ...prev,
                windowDays: Number(e.target.value) as WindowDays,
              }))
            }
            aria-label="Timing window in days"
          >
            <option value={7}>7d</option>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
          </select>
        </label>
        <label className="stx-cards-fleet-timing__control">
          group by
          <select
            className="stx-cards-fleet-timing__select"
            value={state.groupBy}
            onChange={(e) =>
              setState((prev) => ({
                ...prev,
                groupBy: e.target.value as GroupBy,
              }))
            }
            aria-label="Timing group-by dimension"
          >
            <option value="agent">Agent</option>
            <option value="project">Project</option>
            <option value="group">Group</option>
          </select>
        </label>
        <button
          type="button"
          className="stx-cards-fleet-timing__close"
          onClick={() => setState((prev) => ({ ...prev, expanded: false }))}
          aria-label="Collapse fleet timing chart"
          title="collapse"
        >
          ×
        </button>
      </span>
      <div
        className="stx-cards-fleet-timing__chart"
        role="img"
        aria-label="Fleet timing bar chart"
      >
        {chart.keys.length === 0 ? (
          <span className="stx-cards-fleet-timing__empty">
            no rows in window
          </span>
        ) : (
          chart.keys.map((key) => {
            const row = chart.rows[key];
            const medianPct = barWidthPct(
              row.median_started_to_done_s,
              chart.max,
            );
            const p95Pct = barWidthPct(row.p95_started_to_done_s, chart.max);
            const queueLabel =
              row.median_created_to_started_s !== null
                ? formatDurationSeconds(row.median_created_to_started_s)
                : "—";
            const tooltip =
              `${key}\n` +
              `n_tasks_done: ${row.n_tasks_done}\n` +
              `median: ${formatDurationSeconds(row.median_started_to_done_s)}\n` +
              `p95: ${formatDurationSeconds(row.p95_started_to_done_s)}\n` +
              `median_queue_s: ${queueLabel}`;
            return (
              <div
                key={key}
                className="stx-cards-fleet-timing__row"
                title={tooltip}
              >
                <span className="stx-cards-fleet-timing__rowlabel">{key}</span>
                <span className="stx-cards-fleet-timing__bars">
                  <span
                    className={`stx-cards-fleet-timing__bar stx-cards-fleet-timing__bar--median ${barColorToken(medianPct)}`}
                    style={{ width: `${medianPct}%` }}
                    aria-label={`median ${formatDurationSeconds(row.median_started_to_done_s)}`}
                  >
                    <span className="stx-cards-fleet-timing__bartext">
                      {formatDurationSeconds(row.median_started_to_done_s)}
                    </span>
                  </span>
                  <span
                    className={`stx-cards-fleet-timing__bar stx-cards-fleet-timing__bar--p95 ${barColorToken(p95Pct)}`}
                    style={{ width: `${p95Pct}%` }}
                    aria-label={`p95 ${formatDurationSeconds(row.p95_started_to_done_s)}`}
                  >
                    <span className="stx-cards-fleet-timing__bartext">
                      {formatDurationSeconds(row.p95_started_to_done_s)}
                    </span>
                  </span>
                </span>
              </div>
            );
          })
        )}
      </div>
      <span className="stx-cards-fleet-timing__footer">
        {`${payload.n_tasks_in_window} tasks in window · ${payload.n_tasks_missing_timestamps} missing timestamps (diagnostic)`}
      </span>
    </span>
  );
}

export default FleetTimingPanel;
