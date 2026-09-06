/* test__search_suggest.js — node --test unit tests for the autocomplete /
 * Tab-completion engine shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/searchSuggest.js``.
 *
 * Operator TG 12318 / lead a2a `e09e0c886eb94e509f8daa87c23dca2a`
 * (2026-06-12). Extends PR #102 (qualifier syntax) with a suggestion +
 * cursor-insertion layer.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__search_suggest.js
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
    "searchSuggest.js",
  ),
);

const {
  tokenAtCursor,
  keySuggestions,
  valueSuggestions,
  applySuggestion,
  formatSuggestion,
  computeSuggestions,
} = MOD;

const sampleNodes = [
  {
    id: "paper-clew-1",
    project: "paper-scitex-clew",
    agent: "proj-paper-scitex-clew",
    status: "in_progress",
    priority: 1,
    kind: "task",
    host: "spartan",
    scope: "clew",
  },
  {
    id: "paper-clew-2",
    project: "paper-scitex-clew",
    agent: "proj-paper-scitex-clew",
    status: "blocked",
    priority: 2,
    kind: "task",
    host: "spartan",
    scope: "clew",
  },
  {
    id: "hub-1",
    project: "scitex-hub",
    agent: "proj-scitex-hub",
    status: "done",
    priority: 3,
    kind: "task",
    host: "mba",
  },
  {
    id: "compute-1",
    project: "ripple-wm",
    agent: "proj-ripple-wm",
    status: "in_progress",
    priority: 4,
    kind: "compute",
    host: "spartan",
  },
  {
    id: "decide-1",
    project: "paper-scitex-clew",
    agent: null,
    status: "blocked",
    priority: 5,
    kind: "decision",
  },
];
const dataSource = { nodes: sampleNodes };

// === tokenAtCursor ========================================================
test("tokenAtCursor: empty input + cursor at 0 -> empty key token", () => {
  const t = tokenAtCursor("", 0);
  assert.equal(t.kind, "key");
  assert.equal(t.token, "");
  assert.equal(t.prefix, "");
  assert.equal(t.suffix, "");
});

test("tokenAtCursor: typing `pro` at end -> key=pro", () => {
  const t = tokenAtCursor("pro", 3);
  assert.equal(t.kind, "key");
  assert.equal(t.token, "pro");
  assert.equal(t.prefix, "");
  assert.equal(t.suffix, "");
});

test("tokenAtCursor: cursor after `project:pa` -> value=pa, qualifierKey=project", () => {
  const t = tokenAtCursor("project:pa", 10);
  assert.equal(t.kind, "value");
  assert.equal(t.qualifierKey, "project");
  assert.equal(t.token, "pa");
});

test("tokenAtCursor: cursor in middle of `project:foo` (after `pro`) -> key=pro", () => {
  const t = tokenAtCursor("project:foo", 3);
  assert.equal(t.kind, "key");
  assert.equal(t.token, "pro");
});

test("tokenAtCursor: cursor after a space -> empty key token, prefix carries leading run", () => {
  const t = tokenAtCursor("foo ", 4);
  assert.equal(t.kind, "key");
  assert.equal(t.token, "");
  assert.equal(t.prefix, "foo ");
});

test("tokenAtCursor: cursor between two tokens", () => {
  // "project:foo baz" cursor at 12 = start of "baz"
  const t = tokenAtCursor("project:foo baz", 12);
  assert.equal(t.kind, "key");
  assert.equal(t.token, "");
});

test("tokenAtCursor: handles quoted value-prefix", () => {
  const t = tokenAtCursor('project:"pa', 11);
  assert.equal(t.kind, "value");
  assert.equal(t.qualifierKey, "project");
  // Quote stripped from token.
  assert.equal(t.token, "pa");
});

// === keySuggestions =======================================================
test("keySuggestions: `pro` prefix-matches `project:`", () => {
  const sug = keySuggestions("pro");
  const labels = sug.map((s) => s.label);
  assert.ok(labels.includes("project:"));
});

test("keySuggestions: `pri` prefix-matches `priority:`", () => {
  const sug = keySuggestions("pri");
  const labels = sug.map((s) => s.label);
  assert.ok(labels.includes("priority:"));
});

test("keySuggestions: empty prefix returns ALL known qualifiers", () => {
  const sug = keySuggestions("");
  // 11 known qualifiers per PR #102's dictionary.
  assert.ok(sug.length >= 10);
});

test("keySuggestions: unknown prefix returns empty list", () => {
  const sug = keySuggestions("zzz");
  assert.equal(sug.length, 0);
});

test("keySuggestions: alpha-sorted by label", () => {
  const sug = keySuggestions("");
  const labels = sug.map((s) => s.label);
  const sorted = [...labels].sort();
  assert.deepEqual(labels, sorted);
});

test("keySuggestions: every entry has kind=key + value ends in `:`", () => {
  for (const s of keySuggestions("")) {
    assert.equal(s.kind, "key");
    assert.match(s.value, /:$/);
  }
});

// === valueSuggestions =====================================================
test("valueSuggestions: project:pap surfaces paper-scitex-clew (3 tasks)", () => {
  const sug = valueSuggestions("project", "pap", dataSource);
  const labels = sug.map((s) => s.label);
  assert.ok(labels.includes("paper-scitex-clew"));
  const top = sug.find((s) => s.label === "paper-scitex-clew");
  assert.equal(top.count, 3);
});

test("valueSuggestions: project values sorted by freq DESC then alpha", () => {
  const sug = valueSuggestions("project", "", dataSource);
  // paper-scitex-clew(3) > scitex-hub(1) / ripple-wm(1) (alpha tie)
  assert.equal(sug[0].label, "paper-scitex-clew");
  // Remaining two are tied at count=1; alpha order -> paper-… , ripple-wm, scitex-hub
  assert.equal(sug[1].label, "ripple-wm");
  assert.equal(sug[2].label, "scitex-hub");
});

test("valueSuggestions: status uses closed enum, alpha sort", () => {
  const sug = valueSuggestions("status", "", dataSource);
  const labels = sug.map((s) => s.label);
  // VALID_STATUSES = blocked/cancelled/deferred/done/failed/goal/in_progress
  // — 7 entries; capped at 8 so all should appear. `pending` was ABOLISHED
  // 2026-07-10 and is not one of them; this file spent that time asserting 7
  // while the JS list carried 8, and nothing ran to say so.
  assert.equal(labels.length, 7);
  assert.deepEqual(labels, [...labels].sort());
});

test("valueSuggestions: kind enum filters by prefix", () => {
  const sug = valueSuggestions("kind", "com", dataSource);
  assert.equal(sug.length, 1);
  assert.equal(sug[0].label, "compute");
});

test("valueSuggestions: unknown qualifier returns empty list", () => {
  const sug = valueSuggestions("zzz", "foo", dataSource);
  assert.equal(sug.length, 0);
});

test("valueSuggestions: empty prefix returns top values (no filter)", () => {
  const sug = valueSuggestions("agent", "", dataSource);
  assert.ok(sug.length >= 1);
});

test("valueSuggestions: priority includes seen + operator hints", () => {
  const sug = valueSuggestions("priority", "", dataSource);
  const labels = sug.map((s) => s.label);
  assert.ok(labels.includes("1"));
  assert.ok(labels.some((l) => /^[<>]=?\d/.test(l)));
});

test("valueSuggestions: cap at 8 entries", () => {
  // Pad the data source so the field has >8 unique values.
  const big = { nodes: [] };
  for (let i = 0; i < 20; i++) {
    big.nodes.push({ id: `t-${i}`, project: `p-${i}` });
  }
  const sug = valueSuggestions("project", "p-", big);
  assert.equal(sug.length, 8);
});

test("valueSuggestions: value with space gets auto-quoted in commit value", () => {
  const ds = {
    nodes: [{ id: "t-1", project: "my space project" }],
  };
  const sug = valueSuggestions("project", "my", ds);
  assert.equal(sug.length, 1);
  // label is the raw value; the commit value carries the quotes.
  assert.equal(sug[0].label, "my space project");
  assert.equal(sug[0].value, '"my space project"');
});

test("valueSuggestions: substring matching catches `clew` inside `paper-scitex-clew`", () => {
  const sug = valueSuggestions("project", "clew", dataSource);
  assert.ok(sug.some((s) => s.label === "paper-scitex-clew"));
});

// === applySuggestion ======================================================
test("applySuggestion: `pro` + Tab key=project: -> `project:`, cursor at 8", () => {
  const r = applySuggestion("pro", 3, {
    kind: "key",
    label: "project:",
    value: "project:",
  });
  assert.equal(r.newQuery, "project:");
  assert.equal(r.newCursorPos, 8);
});

test("applySuggestion: `project:pap` + Tab value -> `project:paper-scitex-clew`", () => {
  const r = applySuggestion("project:pap", 11, {
    kind: "value",
    label: "paper-scitex-clew",
    value: "paper-scitex-clew",
  });
  assert.equal(r.newQuery, "project:paper-scitex-clew");
  assert.equal(r.newCursorPos, "project:paper-scitex-clew".length);
});

test("applySuggestion: preserves leading prefix + trailing suffix", () => {
  // `foo project:pa baz` cursor=14 (just after `pa`)
  const r = applySuggestion("foo project:pa baz", 14, {
    kind: "value",
    label: "paper-scitex-clew",
    value: "paper-scitex-clew",
  });
  assert.equal(r.newQuery, "foo project:paper-scitex-clew baz");
});

test("applySuggestion: quoted value preserved in output", () => {
  const r = applySuggestion("project:my", 10, {
    kind: "value",
    label: "my space project",
    value: '"my space project"',
  });
  assert.equal(r.newQuery, 'project:"my space project"');
  // Cursor sits at end of inserted value (after the closing quote).
  assert.equal(r.newCursorPos, 'project:"my space project"'.length);
});

test("applySuggestion: null suggestion returns query untouched", () => {
  const r = applySuggestion("project:foo", 5, null);
  assert.equal(r.newQuery, "project:foo");
});

// === formatSuggestion =====================================================
test("formatSuggestion: returns label + hint pair", () => {
  const f = formatSuggestion({
    kind: "key",
    label: "project:",
    hint: "qualify by project",
  });
  assert.equal(f.label, "project:");
  assert.equal(f.hint, "qualify by project");
});

test("formatSuggestion: handles missing hint", () => {
  const f = formatSuggestion({ kind: "value", label: "paper-scitex-clew" });
  assert.equal(f.hint, "");
});

// === computeSuggestions (end-to-end smoke) ================================
test("computeSuggestions: `pro` returns key suggestions", () => {
  const out = computeSuggestions("pro", 3, dataSource);
  assert.ok(out.some((s) => s.label === "project:"));
  assert.equal(out[0].kind, "key");
});

test("computeSuggestions: `project:pap` returns value suggestions", () => {
  const out = computeSuggestions("project:pap", 11, dataSource);
  assert.ok(out.some((s) => s.label === "paper-scitex-clew"));
  assert.equal(out[0].kind, "value");
});

test("computeSuggestions: empty query returns ALL key suggestions", () => {
  const out = computeSuggestions("", 0, dataSource);
  // All 11 qualifiers visible when nothing typed.
  assert.ok(out.length >= 10);
  assert.equal(out[0].kind, "key");
});
