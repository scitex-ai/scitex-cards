/* Unit tests for chat_window.js — the DM render window.
 *
 * WHY THIS FILE EXISTS IN THIS SHAPE. The previous fix for the same operator
 * complaint used `content-visibility: auto` with a guessed
 * `contain-intrinsic-size`. It fixed the typing and made the thread unreadable —
 * the operator could not reach the bottom, because unrendered messages carried an
 * ESTIMATED height and scrolling replaced estimates with real ones, growing total
 * scroll height under them. It was reverted within minutes.
 *
 * So the load-bearing test here is not "does it render fewer nodes" (easy, and it
 * was never the failure) but `preservedScrollTop` and the loader that uses it:
 * after older messages are prepended, does the operator stay where they were?
 * That is the property the last attempt broke, and it went out unverified because
 * the arithmetic and the repaint lived in different places. They are together now
 * and exercised together below.
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");

const win = require(
  path.join(
    __dirname,
    "../../src/scitex_cards/_django/static/scitex_cards/chat/chat_window.js",
  ),
);

/** n messages, distinguishable by id. */
function thread(n) {
  return Array.from({ length: n }, (_, i) => ({ id: "m" + i }));
}

// === the window ==========================================================

test("a thread shorter than the window renders whole", () => {
  // Arrange
  const msgs = thread(10);

  // Act
  const view = win.windowed(msgs, 60);

  // Assert
  assert.strictEqual(view.length, 10);
});

test("a long thread renders only the trailing window", () => {
  // Arrange
  const msgs = thread(222); // the operator's actual thread length

  // Act
  const view = win.windowed(msgs, 60);

  // Assert
  assert.strictEqual(view.length, 60);
});

test("the window keeps the NEWEST messages, not the oldest", () => {
  // Arrange
  const msgs = thread(222);

  // Act
  const view = win.windowed(msgs, 60);

  // Assert
  assert.strictEqual(view[view.length - 1].id, "m221");
});

test("hasOlder is true only while messages sit above the window", () => {
  // Arrange
  const msgs = thread(70);

  // Act
  const more = win.hasOlder(msgs, 60);

  // Assert
  assert.strictEqual(more, true);
});

test("hasOlder is false once the window covers the thread", () => {
  // Arrange
  const msgs = thread(60);

  // Act
  const more = win.hasOlder(msgs, 60);

  // Assert
  assert.strictEqual(more, false);
});

test("growing never overshoots the thread length", () => {
  // Arrange
  const msgs = thread(70);

  // Act
  const next = win.grownSize(msgs, 60, 60);

  // Assert
  assert.strictEqual(next, 70);
});

// === the scroll-position correction ======================================

test("prepending holds the view: the delta is added to scrollTop", () => {
  // Arrange — 400px of older content appeared above the viewport.
  const topBefore = 120;

  // Act
  const next = win.preservedScrollTop(topBefore, 1000, 1400);

  // Assert
  assert.strictEqual(next, 520);
});

test("a zero-growth repaint leaves scrollTop alone", () => {
  // Arrange
  const topBefore = 300;

  // Act
  const next = win.preservedScrollTop(topBefore, 1000, 1000);

  // Assert
  assert.strictEqual(next, 300);
});

test("a shrinking pane never yields a negative scrollTop", () => {
  // Arrange — pathological, but browsers coerce negatives and that hides bugs.
  const topBefore = 10;

  // Act
  const next = win.preservedScrollTop(topBefore, 1000, 500);

  // Assert
  assert.ok(next >= 0, `expected >= 0, got ${next}`);
});

test("wantsOlder fires near the top and not in the middle", () => {
  // Arrange
  const nearTop = 40;
  const middle = 5000;

  // Act
  const pair = [win.wantsOlder(nearTop), win.wantsOlder(middle)];

  // Assert
  assert.deepStrictEqual(pair, [true, false]);
});

// === the loader, end to end against a fake container =====================

/** A container whose scrollHeight grows with the node count, like a real one. */
function fakeContainer(pxPerMessage) {
  return {
    scrollTop: 0,
    rendered: 0,
    px: pxPerMessage,
    get scrollHeight() {
      return this.rendered * this.px;
    },
  };
}

test("scrolling to the top reveals older messages", () => {
  // Arrange
  const state = { messages: thread(222), windowSize: 60 };
  const c = fakeContainer(250);
  c.rendered = 60;
  const loader = win.createScrollUpLoader(c, state, (slice) => {
    c.rendered = slice.length;
  });
  c.scrollTop = 0;

  // Act
  loader.onScroll();

  // Assert
  assert.strictEqual(state.windowSize, 120);
});

test("THE REGRESSION TEST: revealing older messages holds the view still", () => {
  /* 60 messages at 250px = 15000px tall, operator sitting 100px from the top.
   * Growing to 120 adds 60*250 = 15000px ABOVE them, so to stay on the same
   * message scrollTop must become 15100 — not 100, which would snap them to the
   * top of the newly loaded history and lose their place on every grow. */
  // Arrange
  const state = { messages: thread(222), windowSize: 60 };
  const c = fakeContainer(250);
  c.rendered = 60;
  c.scrollTop = 100;
  const loader = win.createScrollUpLoader(c, state, (slice) => {
    c.rendered = slice.length;
  });

  // Act
  loader.grow();

  // Assert
  assert.strictEqual(c.scrollTop, 15100);
});

test("a mid-thread scroll position does not trigger a grow", () => {
  // Arrange
  const state = { messages: thread(222), windowSize: 60 };
  const c = fakeContainer(250);
  c.rendered = 60;
  c.scrollTop = 9000;
  const loader = win.createScrollUpLoader(c, state, () => {
    throw new Error("repaint must not run mid-thread");
  });

  // Act
  loader.onScroll();

  // Assert
  assert.strictEqual(state.windowSize, 60);
});

test("grow is a no-op once the whole thread is rendered", () => {
  // Arrange
  const state = { messages: thread(40), windowSize: 60 };
  const c = fakeContainer(250);
  c.rendered = 40;
  const loader = win.createScrollUpLoader(c, state, () => {
    throw new Error("repaint must not run with nothing older to show");
  });

  // Act
  const grew = loader.grow();

  // Assert
  assert.strictEqual(grew, false);
});

test("repeated grows walk back to the start without overshooting", () => {
  // Arrange
  const state = { messages: thread(222), windowSize: 60 };
  const c = fakeContainer(250);
  c.rendered = 60;
  const loader = win.createScrollUpLoader(c, state, (slice) => {
    c.rendered = slice.length;
  });

  // Act
  for (let i = 0; i < 10; i++) loader.grow();

  // Assert
  assert.strictEqual(state.windowSize, 222);
});
