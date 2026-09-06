/* test__search_query.js — node --test unit tests for the GitHub-style
 * qualifier-syntax parser shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/searchQuery.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__search_query.js
 *
 * No external deps; uses node's built-in test runner (>=18) so the test
 * suite has zero install cost. This file is intentionally JS, not TS —
 * the module under test ships as a plain <script> consumed by the Django
 * template, so we keep the test surface in the same language.
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
    "searchQuery.js",
  ),
);

const { parseSearchQuery, matchesSearchQuery, tokenize } = MOD;

const sampleTasks = [
  {
    id: "paper-clew-1",
    title: "Draft introduction",
    project: "paper-scitex-clew",
    agent: "proj-paper-scitex-clew",
    status: "in_progress",
    priority: 1,
    kind: "task",
    note: "first cut",
  },
  {
    id: "paper-clew-2",
    title: "Run baseline",
    project: "paper-scitex-clew",
    agent: "proj-paper-scitex-clew",
    status: "blocked",
    priority: 2,
    kind: "task",
  },
  {
    id: "hub-1",
    title: "Hub deploy",
    project: "scitex-hub",
    agent: "proj-scitex-hub",
    status: "done",
    priority: 3,
    kind: "task",
  },
  {
    id: "compute-1",
    title: "Spartan job 25754194",
    project: "ripple-wm",
    agent: "proj-ripple-wm",
    status: "in_progress",
    priority: 4,
    kind: "compute",
  },
  {
    id: "decide-1",
    title: "Pick a model name",
    project: "paper-scitex-clew",
    agent: null,
    status: "blocked",
    priority: 5,
    kind: "decision",
  },
];

// === Tokenizer ============================================================
test("tokenize: bare token becomes free text", () => {
  const toks = tokenize("hello world");
  assert.deepEqual(toks, [{ text: "hello" }, { text: "world" }]);
});

test("tokenize: qualifier with colon", () => {
  const toks = tokenize("project:paper-scitex-clew");
  assert.equal(toks.length, 1);
  assert.equal(toks[0].qualifier, "project");
  assert.equal(toks[0].value, "paper-scitex-clew");
});

test("tokenize: photographed pattern with space after colon", () => {
  const toks = tokenize("project: paper-scitex-clew");
  assert.equal(toks.length, 1);
  assert.equal(toks[0].qualifier, "project");
  assert.equal(toks[0].value, "paper-scitex-clew");
});

test("tokenize: quoted value with spaces", () => {
  const toks = tokenize('project:"paper scitex clew"');
  assert.equal(toks.length, 1);
  assert.equal(toks[0].qualifier, "project");
  assert.equal(toks[0].value, "paper scitex clew");
});

test('tokenize: quoted value after space (project: "paper scitex clew")', () => {
  const toks = tokenize('project: "paper scitex clew"');
  assert.equal(toks.length, 1);
  assert.equal(toks[0].qualifier, "project");
  assert.equal(toks[0].value, "paper scitex clew");
});

test("tokenize: mixed qualifier + bare token", () => {
  const toks = tokenize("project:paper-scitex-clew baseline");
  assert.equal(toks.length, 2);
  assert.equal(toks[0].qualifier, "project");
  assert.equal(toks[1].text, "baseline");
});

// === Parser ===============================================================
test("parseSearchQuery: empty input", () => {
  const p = parseSearchQuery("");
  assert.equal(p.qualifiers.length, 0);
  assert.equal(p.free.length, 0);
  assert.equal(p.hasQualifiers, false);
});

test("parseSearchQuery: known qualifier (project alias)", () => {
  const p = parseSearchQuery("project:paper-scitex-clew");
  assert.equal(p.qualifiers.length, 1);
  assert.equal(p.qualifiers[0].canonical, "project");
  assert.equal(p.qualifiers[0].unknown, false);
  assert.equal(p.hasQualifiers, true);
  assert.equal(p.hints[0].label, "project");
});

test("parseSearchQuery: repo alias collapses to project canonical", () => {
  const p = parseSearchQuery("repo:scitex-cards");
  assert.equal(p.qualifiers[0].canonical, "project");
  assert.equal(p.hints[0].label, "project");
});

test("parseSearchQuery: unknown qualifier flagged + suggestion present", () => {
  const p = parseSearchQuery("unknownkey:foo");
  assert.equal(p.qualifiers[0].unknown, true);
  assert.equal(p.hints[0].unknown, true);
  assert.match(p.hints[0].suggestion, /project/);
});

test("parseSearchQuery: unknown status value flagged unknownValue", () => {
  const p = parseSearchQuery("status:totally-fake");
  assert.equal(p.qualifiers[0].unknown, false);
  assert.equal(p.qualifiers[0].unknownValue, true);
  assert.equal(p.hints[0].unknownValue, true);
});

test("parseSearchQuery: multi-qualifier", () => {
  const p = parseSearchQuery("project:paper-scitex-clew status:blocked");
  assert.equal(p.qualifiers.length, 2);
});

test("parseSearchQuery: bare token captured as freeText", () => {
  const p = parseSearchQuery("project:paper-scitex-clew baseline");
  assert.equal(p.qualifiers.length, 1);
  assert.equal(p.freeText, "baseline");
});

// === Matcher (the operator's exact photographed query) ====================
test("matchesSearchQuery: operator's photographed `project: paper-scitex-clew` filters to 3 tasks", () => {
  const p = parseSearchQuery("project: paper-scitex-clew");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  // paper-clew-1, paper-clew-2, decide-1 all carry that project.
  assert.equal(hits.length, 3);
  assert.deepEqual(hits.map((t) => t.id).sort(), [
    "decide-1",
    "paper-clew-1",
    "paper-clew-2",
  ]);
});

test("matchesSearchQuery: project + status AND-combines", () => {
  const p = parseSearchQuery("project:paper-scitex-clew status:blocked");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(hits.map((t) => t.id).sort(), ["decide-1", "paper-clew-2"]);
});

test("matchesSearchQuery: kind:compute exact", () => {
  const p = parseSearchQuery("kind:compute");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(
    hits.map((t) => t.id),
    ["compute-1"],
  );
});

test("matchesSearchQuery: priority:1 exact", () => {
  const p = parseSearchQuery("priority:1");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(
    hits.map((t) => t.id),
    ["paper-clew-1"],
  );
});

test("matchesSearchQuery: priority:<3 range", () => {
  const p = parseSearchQuery("priority:<3");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(hits.map((t) => t.id).sort(), [
    "paper-clew-1",
    "paper-clew-2",
  ]);
});

test("matchesSearchQuery: priority:>=4 range", () => {
  const p = parseSearchQuery("priority:>=4");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(hits.map((t) => t.id).sort(), ["compute-1", "decide-1"]);
});

test("matchesSearchQuery: id substring", () => {
  const p = parseSearchQuery("id:clew");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(hits.map((t) => t.id).sort(), [
    "paper-clew-1",
    "paper-clew-2",
  ]);
});

test("matchesSearchQuery: status:bogus returns zero matches (unknownValue)", () => {
  const p = parseSearchQuery("status:totally-fake");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.equal(hits.length, 0);
});

test("matchesSearchQuery: unknownkey:foo returns zero (so operator sees the typo)", () => {
  const p = parseSearchQuery("unknownkey:foo");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.equal(hits.length, 0);
});

test("matchesSearchQuery: bare token still uses fuzzy on title/note/id", () => {
  const p = parseSearchQuery("baseline");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(
    hits.map((t) => t.id),
    ["paper-clew-2"],
  );
});

test("matchesSearchQuery: agent alias = assignee", () => {
  const p = parseSearchQuery("assignee:proj-scitex-hub");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.deepEqual(
    hits.map((t) => t.id),
    ["hub-1"],
  );
});

test("matchesSearchQuery: quoted value with spaces", () => {
  const p = parseSearchQuery('project:"paper scitex clew"');
  // "paper scitex clew" with spaces won't match "paper-scitex-clew",
  // confirming we don't munge whitespace inside quoted values.
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.equal(hits.length, 0);
});

test("matchesSearchQuery: empty string passes everything", () => {
  const p = parseSearchQuery("");
  const hits = sampleTasks.filter((t) => matchesSearchQuery(t, p));
  assert.equal(hits.length, sampleTasks.length);
});
