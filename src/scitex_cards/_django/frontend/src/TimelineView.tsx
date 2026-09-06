/** TimelineView — the 5th LAYOUT option (operator-direct ask, TG;
 * relayed by lead a2a `d0f7a0e3`, 2026-06-14).
 *
 * Live raster timeline of the whole fleet. ONE screen, the operator sees
 * the fleet in motion:
 *   - X = TIME (sliding window, ~24h default; right edge = now).
 *   - Y = LANES (one row per agent or group; toggleable).
 *   - Each task = a horizontal bar from started_at → ended_at (or → now).
 *   - Dependencies = thin connecting lines between bars.
 *   - Completed bars fade to muted color (still visible until they age out).
 *   - Polls `/timeline` every 30s — same cadence as the CI-status pills.
 *
 * Click a bar -> open the existing NodeDetailPanel drawer (reuses the
 * TableView selectNode flow).
 *
 * Deferred (operator-future asks, NOT this PR — flagged with CARDs):
 *   - Pan / zoom / pinch (kept static; window dropdown changes scope).
 *   - Click-and-drag to reschedule (update is the side-channel today).
 *   - Live WebSocket stream (polling is the floor).
 *   - Sub-second resolution (minute-level is enough). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBoardStore } from "./store/useBoardStore";
import type { StatusColor } from "./types/board";
import {
  eventBarGeometry,
  groupEventsByLane,
  makeTicks,
  parseTimelineTs,
} from "./timelineHelpers";

// Poll cadence — matches FleetCiPills / FleetHostsPanel so the operator's
// "what just changed" cognitive load stays uniform across the board.
const POLL_MS = 30_000;

// SVG layout constants. Lane height stays small so the operator sees
// many agents at once; the bar fills most of the lane so the time-span
// is the dominant visual.
const LANE_HEIGHT = 28;
const BAR_INSET_Y = 4;
const LANE_LABEL_WIDTH = 140;
const AXIS_HEIGHT = 22;
const TICK_COUNT = 6;

type LaneBy = "agent" | "group";
type WindowKey = "1h" | "6h" | "24h" | "7d";

const WINDOW_HOURS: Record<WindowKey, number> = {
  "1h": 1,
  "6h": 6,
  "24h": 24,
  "7d": 168,
};

interface TimelineEvent {
  id: string;
  title: string;
  agent: string | null;
  group: string | null;
  lane: string;
  started_at: string | null;
  ended_at: string | null;
  status: string;
  priority: number | null;
  kind: string | null;
}

interface TimelineEdge {
  source: string;
  target: string;
  kind: "depends_on" | "blocks";
}

interface TimelinePayload {
  events: TimelineEvent[];
  edges: TimelineEdge[];
  window_start: string;
  window_end: string;
  lane_by: LaneBy;
  lanes: string[];
  store_path: string;
}

/** Fetch helper. Lives inline because the api/client module is generic
 * over the board payload — a future refactor can move this there. */
async function fetchTimeline(
  windowKey: WindowKey,
  laneBy: LaneBy,
): Promise<TimelinePayload> {
  const params = new URLSearchParams({
    window_hours: String(WINDOW_HOURS[windowKey]),
    lane_by: laneBy,
  });
  const res = await fetch(`timeline?${params.toString()}`);
  if (!res.ok) {
    // Fail-loud: surface the server's message so the operator notices a
    // broken store rather than seeing a silently-empty raster.
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `timeline ${res.status}`);
  }
  return (await res.json()) as TimelinePayload;
}

/** Pick a fill / stroke for a bar from the per-status color tokens.
 * Completed bars fade — we apply `opacity: 0.55` in the JSX instead of
 * mutating the color so the dark/light theme switch keeps working. */
function colorFor(
  status: string,
  statusColors: Record<string, StatusColor>,
): StatusColor {
  return (
    statusColors[status] ?? {
      fill: "var(--stx-border-strong)",
      stroke: "var(--stx-border-strong)",
      dashed: false,
    }
  );
}

interface BarGeo {
  x: number;
  y: number;
  width: number;
  laneIndex: number;
  event: TimelineEvent;
}

/** The 5th LAYOUT — fleet raster timeline. */
export function TimelineView({
  statusColors,
}: {
  statusColors: Record<string, StatusColor>;
}) {
  const [payload, setPayload] = useState<TimelinePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [laneBy, setLaneBy] = useState<LaneBy>("agent");
  const [windowKey, setWindowKey] = useState<WindowKey>("24h");
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [drawWidth, setDrawWidth] = useState(800);
  const selectNode = useBoardStore((s) => s.selectNode);

  // Re-measure the drawable width on container resize so the raster
  // scales fluidly with the viewport.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.getBoundingClientRect().width - LANE_LABEL_WIDTH - 16;
      setDrawWidth(Math.max(200, w));
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await fetchTimeline(windowKey, laneBy);
      setPayload(next);
      setError(null);
    } catch (e) {
      // Don't blank the prior payload — the operator keeps seeing the
      // last good raster while the error pill explains the gap.
      setError((e as Error).message);
    }
  }, [windowKey, laneBy]);

  // Initial load + 30s polling. Reload on window / lane-by switch too.
  useEffect(() => {
    void load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  // Geometry — purely derived from `payload + drawWidth`. Memoized so
  // re-renders triggered by hover don't recompute the bar layout.
  const layout = useMemo(() => {
    if (!payload) return null;
    const windowStartMs = parseTimelineTs(payload.window_start);
    const windowEndMs = parseTimelineTs(payload.window_end);
    if (windowStartMs == null || windowEndMs == null) return null;
    const nowMs = Date.now();
    const byLane = groupEventsByLane(payload.events);
    // Lane order from the server (sorted) so the axis is stable across polls.
    const lanes = payload.lanes ?? Array.from(byLane.keys());
    const bars: BarGeo[] = [];
    const barIndexById = new Map<string, BarGeo>();
    lanes.forEach((lane, laneIndex) => {
      const items = byLane.get(lane) ?? [];
      for (const ev of items) {
        const startedMs = parseTimelineTs(ev.started_at);
        const endedMs = parseTimelineTs(ev.ended_at);
        const geo = eventBarGeometry(
          startedMs,
          endedMs,
          windowStartMs,
          windowEndMs,
          nowMs,
          drawWidth,
        );
        if (!geo) continue;
        const bar: BarGeo = {
          x: geo.x,
          y: AXIS_HEIGHT + laneIndex * LANE_HEIGHT + BAR_INSET_Y,
          width: Math.max(geo.width, 2),
          laneIndex,
          event: ev,
        };
        bars.push(bar);
        barIndexById.set(ev.id, bar);
      }
    });
    const ticks = makeTicks(windowStartMs, windowEndMs, drawWidth, TICK_COUNT);
    const totalHeight = AXIS_HEIGHT + lanes.length * LANE_HEIGHT + 4;
    return { lanes, bars, ticks, barIndexById, totalHeight };
  }, [payload, drawWidth]);

  return (
    <div className="stx-cards-timeline" ref={containerRef}>
      <div className="stx-cards-timeline__bar">
        <div className="stx-cards-timeline__title">
          Time View — live raster ({payload?.events.length ?? 0} events)
        </div>
        <div className="stx-cards-timeline__controls">
          <label className="stx-cards-timeline__ctl-label">
            Window
            <select
              className="stx-cards-timeline__select"
              value={windowKey}
              onChange={(e) => setWindowKey(e.target.value as WindowKey)}
              aria-label="Window length"
              title="How far back the raster reaches"
            >
              <option value="1h">1h</option>
              <option value="6h">6h</option>
              <option value="24h">24h</option>
              <option value="7d">7d</option>
            </select>
          </label>
          <label className="stx-cards-timeline__ctl-label">
            Lane by
            <select
              className="stx-cards-timeline__select"
              value={laneBy}
              onChange={(e) => setLaneBy(e.target.value as LaneBy)}
              aria-label="Lane projection"
              title="Group rows by agent or by T1.1 group"
            >
              <option value="agent">By Agent</option>
              <option value="group">By Group</option>
            </select>
          </label>
          {error && (
            <span
              className="stx-cards-timeline__error"
              role="alert"
              title={error}
            >
              ! {error}
            </span>
          )}
        </div>
      </div>
      {!payload && !error && (
        <div className="stx-cards-timeline__loading">Loading timeline…</div>
      )}
      {layout && (
        <div className="stx-cards-timeline__scroll">
          <svg
            className="stx-cards-timeline__svg"
            width={LANE_LABEL_WIDTH + drawWidth}
            height={layout.totalHeight}
            role="img"
            aria-label="Fleet timeline raster"
          >
            {/* Time-axis tick labels along the top. */}
            <g className="stx-cards-timeline__axis">
              {layout.ticks.map((tk, i) => (
                <g
                  key={i}
                  transform={`translate(${LANE_LABEL_WIDTH + tk.x}, 0)`}
                >
                  <line
                    x1={0}
                    x2={0}
                    y1={AXIS_HEIGHT - 4}
                    y2={layout.totalHeight}
                    className="stx-cards-timeline__tickline"
                  />
                  <text
                    x={0}
                    y={AXIS_HEIGHT - 8}
                    textAnchor="middle"
                    className="stx-cards-timeline__ticktext"
                  >
                    {tk.label}
                  </text>
                </g>
              ))}
            </g>
            {/* Lane labels + lane background stripes. */}
            <g className="stx-cards-timeline__lanes">
              {layout.lanes.map((lane, i) => {
                const yTop = AXIS_HEIGHT + i * LANE_HEIGHT;
                return (
                  <g key={lane}>
                    <rect
                      x={0}
                      y={yTop}
                      width={LANE_LABEL_WIDTH + drawWidth}
                      height={LANE_HEIGHT}
                      className={`stx-cards-timeline__lane-bg${
                        i % 2 === 0 ? " stx-cards-timeline__lane-bg--even" : ""
                      }`}
                    />
                    <text
                      x={8}
                      y={yTop + LANE_HEIGHT / 2 + 4}
                      className="stx-cards-timeline__lane-label"
                    >
                      {lane}
                    </text>
                  </g>
                );
              })}
            </g>
            {/* Dependency lines — drawn BEFORE the bars so the bars stack
             * on top. The line endpoints are the right edge of the source
             * bar and the left edge of the target bar (depends_on: dep ->
             * dependent). */}
            <g className="stx-cards-timeline__edges">
              {payload?.edges.map((e, i) => {
                const src = layout.barIndexById.get(e.source);
                const tgt = layout.barIndexById.get(e.target);
                if (!src || !tgt) return null;
                const x1 = LANE_LABEL_WIDTH + src.x + src.width;
                const y1 = src.y + (LANE_HEIGHT - BAR_INSET_Y * 2) / 2;
                const x2 = LANE_LABEL_WIDTH + tgt.x;
                const y2 = tgt.y + (LANE_HEIGHT - BAR_INSET_Y * 2) / 2;
                return (
                  <line
                    key={i}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    className={`stx-cards-timeline__edge stx-cards-timeline__edge--${e.kind}`}
                  />
                );
              })}
            </g>
            {/* Event bars — one rect per visible event. Click opens the
             * detail drawer; right-click could open the context menu in a
             * future PR. */}
            <g className="stx-cards-timeline__bars">
              {layout.bars.map((bar) => {
                const c = colorFor(bar.event.status, statusColors);
                const completed = bar.event.ended_at != null;
                return (
                  <g
                    key={bar.event.id}
                    transform={`translate(${LANE_LABEL_WIDTH + bar.x}, ${bar.y})`}
                    className={`stx-cards-timeline__bar${
                      completed ? " stx-cards-timeline__bar--completed" : ""
                    }`}
                    onClick={() => selectNode(bar.event.id)}
                  >
                    <title>
                      {bar.event.title}
                      {"\n"}status: {bar.event.status}
                      {bar.event.started_at
                        ? `\nstarted: ${bar.event.started_at}`
                        : ""}
                      {bar.event.ended_at
                        ? `\ncompleted: ${bar.event.ended_at}`
                        : ""}
                    </title>
                    <rect
                      x={0}
                      y={0}
                      width={bar.width}
                      height={LANE_HEIGHT - BAR_INSET_Y * 2}
                      rx={3}
                      ry={3}
                      style={{
                        fill: c.fill,
                        stroke: c.stroke,
                        opacity: completed ? 0.55 : 1,
                      }}
                    />
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
      )}
    </div>
  );
}
