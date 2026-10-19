/* test__dm_pane.js — node --test unit tests for the DM/chat left pane
 * helpers shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/dmPane.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__dm_pane.js
 *
 * No external deps; uses node's built-in test runner (>=18) so the test
 * suite has zero install cost. This file is intentionally JS, not TS — the
 * module under test ships as a plain <script> consumed by the Django
 * template, so we keep the test surface in the same language.
 *
 * Contract under test (operator layout directive 2026-10-19): DM open by
 * DEFAULT, `?dm=` / `?peer=` URL overrides, existing `dm/*` endpoint shapes,
 * 30s poll cadence, defensive record reads.
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
    "dmPane.js",
  ),
);

test("poll cadence matches the other fleet surfaces (30s)", () => {
  assert.equal(MOD.DM_POLL_MS, 30000);
});

test("default-open: only an explicit '0' closes the pane", () => {
  assert.equal(MOD.dmDefaultOpen(null), true);
  assert.equal(MOD.dmDefaultOpen(undefined), true);
  assert.equal(MOD.dmDefaultOpen(""), true);
  assert.equal(MOD.dmDefaultOpen("1"), true);
  assert.equal(MOD.dmDefaultOpen("0"), false);
});

test("?peer= selects a thread, absent selects nothing", () => {
  assert.equal(MOD.dmPeerFromUrl("?peer=scitex-app"), "scitex-app");
  assert.equal(MOD.dmPeerFromUrl("?q=x&peer=op"), "op");
  assert.equal(MOD.dmPeerFromUrl(""), null);
  assert.equal(MOD.dmPeerFromUrl("?dm=0"), null);
});

test("?dm=0 closes, ?dm=1 opens, absent leaves state alone", () => {
  assert.equal(MOD.dmClosedFromUrl("?dm=0"), true);
  assert.equal(MOD.dmClosedFromUrl("?dm=closed"), true);
  assert.equal(MOD.dmClosedFromUrl("?dm=1"), false);
  assert.equal(MOD.dmClosedFromUrl("?dm=open"), false);
  assert.equal(MOD.dmClosedFromUrl(""), null);
  assert.equal(MOD.dmClosedFromUrl("?peer=x"), null);
});

test("endpoint URLs join the mount-aware API base", () => {
  assert.equal(MOD.dmThreadsUrl("/apps/cards"), "/apps/cards/dm/threads");
  assert.equal(MOD.dmThreadsUrl(""), "/dm/threads");
  assert.equal(
    MOD.dmThreadUrl("/apps/cards", "scitex-app", true),
    "/apps/cards/dm/thread/scitex-app?mark_read=1",
  );
  assert.equal(
    MOD.dmThreadUrl("", "a b", false),
    "/dm/thread/a%20b",
  );
  assert.equal(
    MOD.dmSendUrl("", "scitex-app"),
    "/dm/thread/scitex-app",
  );
});

test("record reads never crash on unknown shapes", () => {
  assert.equal(MOD.dmAuthor({}), "unknown");
  assert.equal(MOD.dmAuthor({ author: "  " }), "unknown");
  assert.equal(MOD.dmAuthor({ author: "op" }), "op");
  assert.equal(MOD.dmAuthor({ from: "agent" }), "agent");
  assert.equal(MOD.dmBody({}), "");
  assert.equal(MOD.dmBody({ body: "hi" }), "hi");
  assert.equal(MOD.dmBody({ text: "legacy" }), "legacy");
  assert.equal(MOD.dmTs({}), "");
  assert.equal(MOD.dmTs({ ts: "2026-10-19T05:00:00Z" }), "2026-10-19T05:00:00Z");
});

test("timestamp formatting falls back to the raw string", () => {
  assert.equal(MOD.formatDmTs(""), "");
  assert.equal(MOD.formatDmTs("not-a-date"), "not-a-date");
  const formatted = MOD.formatDmTs("2026-10-19T05:00:00Z");
  assert.ok(formatted.includes("2026") || formatted.includes("Oct"));
});

test("unread total sums positive counters only", () => {
  assert.equal(MOD.dmUnreadTotal([]), 0);
  assert.equal(
    MOD.dmUnreadTotal([{ unread: 2 }, { unread: 0 }, { unread: 3 }]),
    5,
  );
  assert.equal(MOD.dmUnreadTotal([{ unread: -1 }, {}]), 0);
});
