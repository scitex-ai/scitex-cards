/* test__app_router.js — node --test unit tests for the PURE pane helpers in
 * ``src/scitex_cards/_django/static/scitex_cards/app_router.js`` (the
 * unified #dm/#board hash router).
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__app_router.js
 *
 * Only the pure parts are covered: paneForHash (hash -> pane name) and
 * applyPane over a minimal fake root (querySelector/querySelectorAll +
 * classList/setAttribute shims). The live-DOM wiring (hashchange, click
 * interception) is browser-only and short-circuits under node — mount()
 * returns null without a document, pinned below.
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
    "app_router.js",
  ),
);

const { paneForHash, applyPane, mount } = MOD;

function fakeEl() {
  const classes = new Set();
  const attrs = {};
  return {
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      has: (c) => classes.has(c),
    },
    setAttribute: (k, v) => {
      attrs[k] = v;
    },
    getAttribute: (k) => attrs[k] || "",
    removeAttribute: (k) => {
      delete attrs[k];
    },
    _classes: classes,
    _attrs: attrs,
  };
}

function fakeRoot() {
  const panes = { "#dm-pane": fakeEl(), "#board-pane": fakeEl() };
  const links = [fakeEl(), fakeEl()];
  links[0].setAttribute("href", "/dm");
  links[1].setAttribute("href", "/board");
  return {
    querySelector: (sel) => panes[sel] || null,
    querySelectorAll: (sel) =>
      sel === ".stx-cards-switcher__item" ? links : [],
    _panes: panes,
    _links: links,
  };
}

test("paneForHash: #dm and empty hash land on dm", () => {
  assert.equal(paneForHash("#dm"), "dm");
  assert.equal(paneForHash(""), "dm");
  assert.equal(paneForHash(null), "dm");
});

test("paneForHash: #board lands on board", () => {
  assert.equal(paneForHash("#board"), "board");
});

test("paneForHash: unknown hash falls back to dm, never throws", () => {
  assert.equal(paneForHash("#nope"), "dm");
  assert.doesNotThrow(() => paneForHash(undefined));
});

test("applyPane: shows the named pane, hides the other", () => {
  const root = fakeRoot();
  const state = applyPane(root, "board");
  assert.deepEqual(state, { dm: false, board: true });
  assert.ok(!("hidden" in root._panes["#board-pane"]._attrs));
  assert.equal(root._panes["#dm-pane"]._attrs.hidden, "");
});

test("applyPane: moves the switcher highlight with the pane", () => {
  const root = fakeRoot();
  applyPane(root, "board");
  assert.ok(root._links[1]._classes.has("stx-cards-switcher__item--on"));
  assert.ok(!root._links[0]._classes.has("stx-cards-switcher__item--on"));
  applyPane(root, "dm");
  assert.ok(root._links[0]._classes.has("stx-cards-switcher__item--on"));
  assert.ok(!root._links[1]._classes.has("stx-cards-switcher__item--on"));
});

test("mount: returns null without a document (node has none)", () => {
  assert.equal(mount(null), null);
  assert.equal(mount({}), null);
});
