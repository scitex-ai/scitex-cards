/* Unit tests for searchDebounce.js — the keystroke→rebuild scheduling policy.
 *
 * Regression cover for the operator's 2026-07-30 report: the caret lagged the
 * keyboard because render() ran synchronously from `oninput`, which fires before
 * the browser paints. These tests pin the POLICY (one trailing rebuild per
 * burst); the handler wiring lives in board_v3.html.
 *
 * The load-bearing test is `a burst of keystrokes rebuilds exactly once` — a
 * leading-edge or non-collapsing implementation passes nothing else here but
 * would still make the first keystroke of every burst pay full price, which is
 * the cost being removed.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const { createSearchDebouncer, SEARCH_RENDER_DEBOUNCE_MS } = require(
  path.join(
    __dirname,
    "../../src/scitex_cards/_django/static/scitex_cards/board_v3/searchDebounce.js",
  ),
);

/** A controllable clock, so the tests assert scheduling rather than sleep. */
function fakeTimers() {
  let now = 0;
  let seq = 0;
  const queued = new Map();
  return {
    setTimeout(fn, ms) {
      const id = ++seq;
      queued.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimeout(id) {
      queued.delete(id);
    },
    advance(ms) {
      now += ms;
      for (const [id, t] of [...queued.entries()]) {
        if (t.at <= now) {
          queued.delete(id);
          t.fn();
        }
      }
    },
  };
}

test("a burst of keystrokes rebuilds exactly once", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  let calls = 0;

  // Act — six keystrokes 20ms apart, then the burst ends.
  for (let i = 0; i < 6; i++) {
    d.schedule(() => { calls++; });
    timers.advance(20);
  }
  timers.advance(120);

  // Assert
  assert.strictEqual(calls, 1);
});

test("nothing runs while the keystrokes are still arriving", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  let calls = 0;

  // Act — keep typing faster than the delay, never let it settle.
  for (let i = 0; i < 10; i++) {
    d.schedule(() => { calls++; });
    timers.advance(100);
  }

  // Assert
  assert.strictEqual(calls, 0);
});

test("the rebuild runs after the delay elapses", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  let calls = 0;

  // Act
  d.schedule(() => { calls++; });
  timers.advance(120);

  // Assert
  assert.strictEqual(calls, 1);
});

test("the LAST scheduled callback is the one that runs", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  const ran = [];

  // Act — a later schedule replaces the pending earlier one.
  d.schedule(() => ran.push("stale"));
  timers.advance(50);
  d.schedule(() => ran.push("fresh"));
  timers.advance(120);

  // Assert
  assert.deepStrictEqual(ran, ["fresh"]);
});

test("cancel drops a pending rebuild", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  let calls = 0;
  d.schedule(() => { calls++; });

  // Act
  d.cancel();
  timers.advance(1000);

  // Assert
  assert.strictEqual(calls, 0);
});

test("pending reports true only while a rebuild is queued", () => {
  // Arrange
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);

  // Act
  d.schedule(() => {});
  const during = d.pending();
  timers.advance(120);

  // Assert
  assert.deepStrictEqual([during, d.pending()], [true, false]);
});

test("a throwing callback does not wedge the debouncer", () => {
  // Arrange — pending() must not lie forever, and the next burst must work.
  const timers = fakeTimers();
  const d = createSearchDebouncer(120, timers);
  d.schedule(() => { throw new Error("render blew up"); });

  // Act
  assert.throws(() => timers.advance(120));

  // Assert
  assert.strictEqual(d.pending(), false);
});

test("the default delay stays under the 150ms that reads as instant", () => {
  // Arrange
  const limit = 150;

  // Act
  const actual = SEARCH_RENDER_DEBOUNCE_MS;

  // Assert
  assert.ok(actual < limit, `${actual}ms must stay under ${limit}ms`);
});
