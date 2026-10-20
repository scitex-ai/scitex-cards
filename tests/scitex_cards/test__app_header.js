/* test__app_header.js — node --test unit tests for the PURE adopt helper in
 * ``src/scitex_cards/_django/static/scitex_cards/app_header.js`` (moves the
 * server-rendered picker into the SDK AppHeader's canonical slot).
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__app_header.js
 *
 * Only the pure adopt() is covered, against a fake root + fake SDK api.
 * The live-DOM wiring (DOMContentLoaded auto-run) is browser-only.
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
    "app_header.js",
  ),
);

const { adopt } = MOD;

function fakeEl() {
  return {
    children: [],
    appendChild(c) {
      this.children.push(c);
    },
  };
}

function fakeRoot(withPicker) {
  const picker = withPicker ? fakeEl() : null;
  return {
    querySelector: (sel) =>
      sel === "[data-stx-project-picker]" ? picker : null,
    _picker: picker,
  };
}

function fakeApi(withSlot = true) {
  const slot = withSlot ? fakeEl() : null;
  return {
    mounted: false,
    mount() {
      this.mounted = true;
    },
    get() {
      return { projectSlot: slot };
    },
    _slot: slot,
  };
}

test("adopt: moves the picker into the canonical slot", () => {
  const root = fakeRoot(true);
  const api = fakeApi(true);
  const out = adopt(root, api);
  assert.equal(out, "adopted");
  assert.equal(api._slot.children.length, 1);
  assert.equal(api._slot.children[0], root._picker);
});

test("adopt: mounts the SDK header first (deterministic order)", () => {
  const root = fakeRoot(true);
  const api = fakeApi(true);
  adopt(root, api);
  assert.equal(api.mounted, true);
});

test("adopt: no picker renders nothing to move", () => {
  assert.equal(adopt(fakeRoot(false), fakeApi(true)), null);
});

test("adopt: missing SDK api leaves the picker where the server put it", () => {
  const root = fakeRoot(true);
  assert.equal(adopt(root, null), "left-in-place");
  assert.equal(adopt(root, undefined), "left-in-place");
});

test("adopt: header without a slot leaves the picker in place", () => {
  const root = fakeRoot(true);
  assert.equal(adopt(root, fakeApi(false)), "left-in-place");
});

test("adopt: null root never throws", () => {
  assert.doesNotThrow(() => adopt(null, fakeApi(true)));
});
