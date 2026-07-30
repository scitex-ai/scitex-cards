/* chat_window.js — WHICH messages the DM pane renders, and where scroll lands.
 *
 * Pure module, same contract as chat_diff.js: no DOM, no fetch, no state. The
 * glue lives in chat.js; the policy and the arithmetic live here so both can be
 * unit-tested under `node --test`.
 *
 * WHY A WINDOW AT ALL. Operator 2026-07-30: typing lags in a HEAVY thread and is
 * comfortable in a light one. Measured — operator::scitex-cards 222 msgs /
 * 86,859 B lags, operator::scitex-hpc 51 msgs / 18,761 B does not. Every message
 * sat in the DOM, so each keystroke paid style+layout across the whole tree, and
 * the composer auto-sizes by reading scrollHeight, which forces that layout
 * synchronously. The cost scaled with thread length rather than with anything in
 * the input handler.
 *
 * THE PREVIOUS ATTEMPT IS WHY THIS ONE IS SHAPED LIKE THIS. `content-visibility:
 * auto` fixed the typing and made the thread unreadable: unrendered messages got
 * an ESTIMATED height, the estimate was ~4x too small, so scrolling replaced
 * estimates with real heights, total scroll height grew, and the bottom receded
 * faster than the operator could reach it. Reverted within minutes.
 *
 * So: this module renders REAL nodes only. Every height is a real height, scroll
 * height is stable, and there is NO estimated quantity whose wrongness can move
 * the bottom. That is a difference in mechanism, not a smaller guess.
 *
 * The operator chose the behaviour: 「古い分はスクロールアップに合わせて自動で読め
 * ばよい」 — older messages appear automatically on scroll-up, not behind a button.
 */

//: How many trailing messages render on open. 60 comfortably exceeds a screenful
//: at any plausible bubble size, so the operator never sees a short thread.
const WINDOW_INITIAL = 60;

//: How many more are revealed per scroll-up. Same as the initial size: one grow
//: roughly doubles a fresh window, so reaching the top of a long thread takes
//: few grows without any single grow being expensive.
const WINDOW_STEP = 60;

//: Distance from the top that counts as "wants older". Deliberately LARGER than
//: chat.js's 40px stick-to-bottom threshold: this one triggers a prepend, so
//: firing slightly early is invisible while firing late shows a blank gap.
const GROW_THRESHOLD_PX = 160;

/** The trailing slice of `messages` that should be rendered.
 *
 * `messages` stays the FULL thread everywhere else — this is a view for
 * rendering only, so search, export and the public accessor keep seeing all of
 * it. Returns the input unchanged when it already fits, so a short thread is
 * never copied.
 */
function windowed(messages, windowSize) {
  const all = messages || [];
  if (all.length <= windowSize) return all;
  return all.slice(all.length - windowSize);
}

/** True when messages exist above the current window. */
function hasOlder(messages, windowSize) {
  return (messages || []).length > windowSize;
}

/** The next window size, never overshooting the thread length. */
function grownSize(messages, windowSize, step) {
  const total = (messages || []).length;
  const next = windowSize + (typeof step === "number" ? step : WINDOW_STEP);
  return next > total ? total : next;
}

/** Should a scroll position this close to the top reveal older messages? */
function wantsOlder(scrollTop, threshold) {
  const limit = typeof threshold === "number" ? threshold : GROW_THRESHOLD_PX;
  return scrollTop <= limit;
}

/** Where scrollTop must land after PREPENDING content, to hold the view still.
 *
 * THE LOAD-BEARING CALCULATION of this module. Prepending shifts everything down
 * by exactly how much the content above the viewport grew, so the correction is
 * that delta — MEASURED from real scrollHeight before and after, never estimated.
 * An estimate here is precisely the defect that got the previous attempt
 * reverted. Without the correction, every grow snaps the view to the top, which
 * reads as the thread throwing you around.
 *
 * Clamped at 0 because a shrinking pane (heightAfter < heightBefore) must not
 * produce a negative offset, which browsers coerce to 0 anyway — being explicit
 * keeps the intent readable rather than relying on that coercion.
 */
function preservedScrollTop(topBefore, heightBefore, heightAfter) {
  const corrected = topBefore + (heightAfter - heightBefore);
  return corrected < 0 ? 0 : corrected;
}

/** The scroll-up loader: reveals older messages and holds the view still.
 *
 * `container` is the scrolling element (the only DOM here — read for scrollTop /
 * scrollHeight and written once). `state` is any object with `messages` and
 * `windowSize`; `windowSize` is mutated in place. `repaint(slice)` rebuilds the
 * pane. A test passes a fake container, a plain state object and a recording
 * repaint — no DOM required.
 *
 * THE MUTATION AND ITS SCROLL CORRECTION LIVE TOGETHER on purpose. Splitting them
 * — arithmetic in one place, repaint in another — is how the previous attempt
 * shipped a scroll behaviour nothing had exercised end to end.
 */
function createScrollUpLoader(container, state, repaint) {
  function grow() {
    if (!hasOlder(state.messages, state.windowSize)) return false;
    const heightBefore = container.scrollHeight;
    const topBefore = container.scrollTop;
    state.windowSize = grownSize(state.messages, state.windowSize);
    repaint(windowed(state.messages, state.windowSize));
    container.scrollTop = preservedScrollTop(
      topBefore,
      heightBefore,
      container.scrollHeight,
    );
    return true;
  }
  return {
    grow,
    onScroll: function () {
      if (wantsOlder(container.scrollTop)) grow();
    },
  };
}

const _chatWindowApi = {
  WINDOW_INITIAL,
  WINDOW_STEP,
  GROW_THRESHOLD_PX,
  windowed,
  hasOlder,
  grownSize,
  wantsOlder,
  preservedScrollTop,
  createScrollUpLoader,
};

if (typeof globalThis !== "undefined") {
  globalThis.STX = globalThis.STX || {};
  globalThis.STX.chatWindow = _chatWindowApi;
  // chat.js reads window.ChatDiff by bare name; match that local convention so
  // the two sibling modules are reached the same way.
  globalThis.ChatWindow = _chatWindowApi;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = _chatWindowApi;
}
