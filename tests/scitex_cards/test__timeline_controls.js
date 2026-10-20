/* test__timeline_controls.js — node --test unit tests for the PURE HTML
 * builders shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/timelineControls.js``
 * (the timeline controls row + the dependency-edge legend).
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__timeline_controls.js
 *
 * No external deps; uses node's built-in test runner (>=18). JS, not TS —
 * the module ships as a plain <script> for the Django board_v3 template
 * (mirrors test__timeline_select.js). Only the PURE builders are covered;
 * the hover-highlight DOM wiring is browser-only and short-circuited under
 * node, so it has no automated test here (visual verification only).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const MOD = require(
  path.resolve(
    __dirname,
    "..",
    "..",
    "src",
    "scitex_cards",
    "_django",
    "static",
    "scitex_cards",
    "board_v3",
    "timelineControls.js",
  ),
);

const { controlsHtml, edgeLegendHtml } = MOD;

// A faithful stand-in for the page's escapeHtml so the assertions exercise
// the same escaping path the browser uses.
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c];
  });
}

test("edgeLegendHtml: both swatches reuse the real .tl-edge--* classes", () => {
  const html = edgeLegendHtml(esc);
  assert.match(html, /tl-edge tl-edge--depends/);
  assert.match(html, /tl-edge tl-edge--blocks/);
});

test("edgeLegendHtml: labels are 'depends on' + 'blocks'", () => {
  const html = edgeLegendHtml(esc);
  assert.match(html, /depends on/);
  assert.match(html, /blocks/);
});

test("edgeLegendHtml: has an aria-label for the key", () => {
  const html = edgeLegendHtml(esc);
  assert.match(html, /aria-label="Edge key:/);
  assert.match(html, /role="img"/);
});

test("edgeLegendHtml: stable legend container class", () => {
  assert.match(edgeLegendHtml(esc), /class="tl-edge-legend"/);
});

test("edgeLegendHtml: no escape fn provided → still callable, no throw", () => {
  assert.doesNotThrow(() => edgeLegendHtml());
  assert.match(edgeLegendHtml(), /tl-edge--depends/);
});

test("controlsHtml: agent/project rasters include the edge legend", () => {
  const agent = controlsHtml(
    { view: "agent", windowKey: "1d" },
    "3 events",
    esc,
  );
  const project = controlsHtml(
    { view: "project", windowKey: "1w" },
    "5 events",
    esc,
  );
  assert.match(agent, /tl-edge-legend/);
  assert.match(project, /tl-edge-legend/);
});

test("controlsHtml: simple view OMITS the edge legend (no edges there)", () => {
  const simple = controlsHtml(
    { view: "simple", windowKey: "1d" },
    "7 tasks",
    esc,
  );
  assert.ok(!simple.includes("tl-edge-legend"));
});

test("controlsHtml: renders the View tab group and count", () => {
  const html = controlsHtml(
    { view: "agent", windowKey: "1m" },
    "2 events",
    esc,
  );
  assert.match(html, /role="tablist"/);
  assert.match(html, /aria-label="Timeline view"/);
  assert.match(html, /class="tl-count">2 events</);
});

test("controlsHtml: View is a tab group, not a select", () => {
  const html = controlsHtml({ view: "agent", windowKey: "1d" }, "x", esc);
  assert.ok(!html.includes("setTimelineView(this.value)"));
  assert.ok(!html.includes('<option value="agent"'));
});

test("controlsHtml: Window is a D/W/M tab group, not a select", () => {
  const html = controlsHtml({ view: "agent", windowKey: "1d" }, "x", esc);
  assert.match(html, /role="tablist"/);
  assert.match(html, /aria-label="Time window"/);
  assert.ok(!html.includes("setTimelineWindow(this.value)"));
  assert.ok(!html.includes('<option value="1d"'));
  assert.ok(!html.includes("<select"));
});

test("controlsHtml: three window tabs wiring setTimelineWindow per key", () => {
  const html = controlsHtml({ view: "agent", windowKey: "1d" }, "x", esc);
  assert.match(html, /data-window="1d"/);
  assert.match(html, /data-window="1w"/);
  assert.match(html, /data-window="1m"/);
  assert.match(html, /onclick="setTimelineWindow\('1d'\)"/);
  assert.match(html, /onclick="setTimelineWindow\('1w'\)"/);
  assert.match(html, /onclick="setTimelineWindow\('1m'\)"/);
});

test("controlsHtml: active window tab is marked, others are not", () => {
  const html = controlsHtml(
    { view: "project", windowKey: "1w" },
    "x",
    esc,
  );
  assert.match(html, /data-window="1w" aria-selected="true"/);
  assert.match(html, /tl-window-tab--on/);
  assert.match(html, /data-window="1d" aria-selected="false"/);
});

test("controlsHtml: three view tabs wiring setTimelineView per key", () => {
  const html = controlsHtml({ view: "agent", windowKey: "1d" }, "x", esc);
  assert.match(html, /data-view="agent"/);
  assert.match(html, /data-view="project"/);
  assert.match(html, /data-view="simple"/);
  assert.match(html, /onclick="setTimelineView\('project'\)"/);
});

test("controlsHtml: active view tab is marked, others are not", () => {
  const html = controlsHtml(
    { view: "simple", windowKey: "1d" },
    "x",
    esc,
  );
  assert.match(html, /data-view="simple" aria-selected="true"/);
  assert.match(html, /data-view="agent" aria-selected="false"/);
});

test("controlsHtml: surfaces TL.error in a .tl-error span", () => {
  const html = controlsHtml(
    { view: "agent", windowKey: "1d", error: "boom" },
    "x",
    esc,
  );
  assert.match(html, /class="tl-error"[^>]*>! boom</);
});

test("controlsHtml: error text is escaped (no raw injection)", () => {
  const html = controlsHtml(
    { view: "agent", windowKey: "1d", error: "<img src=x onerror=1>" },
    "x",
    esc,
  );
  assert.ok(!html.includes("<img src=x"));
  assert.match(html, /&lt;img src=x/);
});

test("controlsHtml: missing TL / esc → no throw, still renders count", () => {
  assert.doesNotThrow(() => controlsHtml());
  assert.match(controlsHtml(undefined, "9 events"), /9 events/);
});
