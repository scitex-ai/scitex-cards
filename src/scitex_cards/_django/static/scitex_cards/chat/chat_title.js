/* Unread count in the BROWSER TAB TITLE — "(3) DM — SciTeX Cards v0.17.10".
 *
 * Operator, 2026-07-29 (TG), verbatim: 「新着がある場合、ページタイトルに新着
 * メッセージ数（未読メッセージ数）を出してください。多少点滅などエフェクトが
 * あっても良いかもです。」 They are migrating off Telegram onto this page, and a
 * background tab that looks identical whether or not an agent has written is the
 * one thing Telegram did for them that this page did not.
 *
 * ONE SOURCE OF TRUTH FOR UNREAD, and it is not this file. `/dm/threads` returns
 * `agents[].unread` (handlers/dm.py reads it off the thread store's per-peer
 * cursor); chat.js already polls that list every 10s and paints the numeric
 * badges beside each peer. This module is handed THAT SAME ARRAY and sums it.
 * It never fetches, never counts messages, and never remembers an unread number
 * across a poll — so the tab and the badges cannot disagree. Do NOT give it its
 * own request: a second reader of the same fact is a second answer waiting to
 * happen, and the two would drift the moment `mark_read` lands between them.
 *
 * THE FLASH IS BOUNDED, BY DESIGN. A tab that blinks forever is hostile — it is
 * unreadable in the tab strip and it never stops asking. So: the alternation
 * runs a fixed FLASH_ALTERNATIONS half-steps and then SETTLES on the count
 * form, permanently. It also only fires when the count went UP: re-announcing a
 * standing unread every 10s poll would be the forever-blink by another route.
 *
 * REDUCED MOTION IS A HARD STOP, NOT A DIMMER. `prefers-reduced-motion: reduce`
 * means no alternation at all — the count still shows (it is information, not
 * decoration), it simply appears and stays. Vestibular triggers do not care that
 * the motion is "only" in the tab strip, and this is the one accessibility knob
 * a title animation can respond to.
 *
 * Consumed two ways, hence the UMD-lite tail (same contract as chat_diff.js):
 *   - browser: <script src=chat_title.js> before chat.js -> window.ChatTitle
 *   - node (tests): require() -> module.exports
 *
 * The DECISIONS (totalUnread / titleFor / flashPlan) are pure, so the test suite
 * runs the real functions under node rather than a hand-ported copy. `mount` is
 * the only part that touches a document.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ChatTitle = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Beyond this the exact number stops being useful and starts eating the tab
   * strip, where ~15 characters are legible. "(99+)" says the same thing. */
  var MAX_SHOWN = 99;

  /* Half-steps of the alternation: count, plain, count, plain — then settle on
   * the count. Four is two blinks: enough to catch an eye pointed elsewhere,
   * short of the flashing the operator would have to sit and watch. */
  var FLASH_ALTERNATIONS = 4;

  /* Slow enough to read the title at each step. Under ~500ms the tab reads as
   * strobing rather than as a notification, and fast alternation is exactly
   * what the reduced-motion preference exists to suppress. */
  var FLASH_INTERVAL_MS = 700;

  /* Sum the per-peer unread counts the DM list already carries.
   *
   * Defensive about the shape because the peer list is server data: a missing,
   * null or negative `unread` contributes 0 rather than turning the whole title
   * into "(NaN)" — a badge that lies about being broken is worse than a zero.
   */
  function totalUnread(agents) {
    if (!agents || !agents.length) return 0;
    var total = 0;
    for (var i = 0; i < agents.length; i++) {
      var raw = agents[i] ? agents[i].unread : 0;
      var n = typeof raw === "number" && isFinite(raw) && raw > 0 ? raw : 0;
      total += Math.floor(n);
    }
    return total;
  }

  function badgeFor(count) {
    return count > MAX_SHOWN ? String(MAX_SHOWN) + "+" : String(count);
  }

  /* The title the tab should carry for `count` unread.
   *
   * The count goes in FRONT: a tab strip truncates from the right, so a suffix
   * is the first thing to disappear on the narrow tab this page will actually
   * be one of. Zero renders the base title unchanged — no "(0)", which would
   * make "nothing new" look like a state that needs attention.
   */
  function titleFor(base, count) {
    var plain = String(base === undefined || base === null ? "" : base);
    if (!count || count <= 0) return plain;
    return "(" + badgeFor(count) + ") " + plain;
  }

  /* How much alternation a count change earns. Pure, so the two rules that make
   * the flash non-hostile are testable without a browser:
   *   - it fires only on an INCREASE (a poll that repeats a standing unread, or
   *     one that clears it, changes the title but does not animate);
   *   - it is silent under reduced motion.
   * `alternations: 0` means "set the title and stop", never "skip the count".
   */
  function flashPlan(previous, next, reducedMotion) {
    var quiet = { alternations: 0, intervalMs: FLASH_INTERVAL_MS };
    if (reducedMotion) return quiet;
    if (!(next > previous)) return quiet;
    return { alternations: FLASH_ALTERNATIONS, intervalMs: FLASH_INTERVAL_MS };
  }

  /* Read the accessibility preference off the live window.
   *
   * Absent matchMedia (or a throwing one) means "no stated preference", which
   * is the same answer as an unset preference — this must never be the reason
   * the count fails to appear.
   */
  function prefersReducedMotion(win) {
    if (!win || typeof win.matchMedia !== "function") return false;
    try {
      var query = win.matchMedia("(prefers-reduced-motion: reduce)");
      return !!(query && query.matches);
    } catch (err) {
      return false;
    }
  }

  /* Own the document title for as long as the page is open.
   *
   * The BASE title is captured once, at mount, from whatever the template
   * rendered — so the version string and the DM wording live in chat.html and
   * this file never spells either. Re-reading it later would eventually capture
   * an already-prefixed title and compound "(3) (3) DM …".
   *
   * Returns `{ update, stop }`. `update(agents)` takes the peer list verbatim
   * from the /dm/threads poll.
   */
  function mount(options) {
    var opts = options || {};
    var doc =
      opts.document || (typeof document !== "undefined" ? document : null);
    var win = opts.window || (typeof window !== "undefined" ? window : null);
    if (!doc) return null;

    var base = String(doc.title || "");
    var shown = 0; // the count the last update painted
    var timer = null;

    function settle(count) {
      doc.title = titleFor(base, count);
    }

    function stop() {
      if (timer !== null && win && win.clearInterval) win.clearInterval(timer);
      timer = null;
    }

    function flash(count, steps, intervalMs) {
      stop();
      if (!win || typeof win.setInterval !== "function") {
        settle(count);
        return;
      }
      var left = steps;
      var lit = true;
      settle(count);
      timer = win.setInterval(function () {
        left -= 1;
        lit = !lit;
        /* The LAST step always lands on the count, whatever `lit` says — the
         * alternation is the announcement, the count is the state, and the
         * state is what the tab keeps. */
        if (left <= 0) {
          stop();
          settle(count);
          return;
        }
        doc.title = lit ? titleFor(base, count) : base;
      }, intervalMs);
    }

    function update(agents) {
      var count = totalUnread(agents);
      var plan = flashPlan(shown, count, prefersReducedMotion(win));
      shown = count;
      if (plan.alternations > 0) {
        flash(count, plan.alternations, plan.intervalMs);
        return;
      }
      stop();
      settle(count);
    }

    return { update: update, stop: stop };
  }

  return {
    MAX_SHOWN: MAX_SHOWN,
    FLASH_ALTERNATIONS: FLASH_ALTERNATIONS,
    FLASH_INTERVAL_MS: FLASH_INTERVAL_MS,
    totalUnread: totalUnread,
    titleFor: titleFor,
    flashPlan: flashPlan,
    prefersReducedMotion: prefersReducedMotion,
    mount: mount,
  };
});

/* EOF */
