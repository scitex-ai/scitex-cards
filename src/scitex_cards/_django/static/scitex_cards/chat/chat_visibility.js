/* Per-reader conversation visibility — the PURE half of hide/unhide
 * (card cards-dm-spaces-crud-hide-20261022).
 *
 * `GET /dm/threads?show=all` answers every row with a `hidden` flag; these
 * functions decide what the list shows from that payload. DOM-free so node
 * can exercise the REAL file (same contract as chat_diff.js / chat_filter.js:
 * a pure function kept anywhere else is a pure function nothing tests).
 *
 * iOS shape this implements:
 *   - visible rows, then ONE disclosure ("Hidden (N)") at the list's end;
 *   - hidden rows render only while the disclosure is open;
 *   - unread hidden threads count into the disclosure label, so a hidden
 *     conversation can never silently hold the operator's mail.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_visibility.js> -> window.ChatVisibility
 *   - node (tests): require() -> module.exports
 *
 * Plain browser JS, no build step, no dependencies (line-limit discipline:
 * js <512 lines).
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ChatVisibility = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Width (px) of the revealed row action. The swipe must pass half of it
   * to snap open — a full-width drag to reveal anything reads as stuck. */
  var ACTION_WIDTH = 72;
  var OPEN_THRESHOLD = ACTION_WIDTH / 2;

  /* Split a /dm/threads payload into the two rendered groups. Missing or
   * non-boolean `hidden` counts as visible: the flag is new (v16) and an
   * older payload must not hide rows the operator never hid. */
  function partition(agents) {
    var visible = [];
    var hidden = [];
    (agents || []).forEach(function (a) {
      if (a && a.hidden === true) hidden.push(a);
      else visible.push(a);
    });
    return { visible: visible, hidden: hidden };
  }

  /* Unread total across the hidden group, for the disclosure label. Counts
   * the same `unread` field the drawer badges render — one fact, so the
   * disclosure and a badge can never disagree about a thread. */
  function hiddenUnread(hidden) {
    var total = 0;
    (hidden || []).forEach(function (a) {
      var n = a && typeof a.unread === "number" ? a.unread : 0;
      if (n > 0) total += n;
    });
    return total;
  }

  /* Swipe end-state for a horizontal drag of `dx` px (negative = left).
   * Pure so the threshold is pinned: left past half the action snaps open,
   * anything else snaps shut. Vertical scrolling never opens — the caller
   * only feeds this once horizontal wins the gesture race. */
  function swipeDecision(dx) {
    return dx <= -OPEN_THRESHOLD ? "open" : "close";
  }

  /* Label for the Hidden disclosure. The unread count rides along only when
   * nonzero — "(0)" would make "nothing new" read as a state. */
  function disclosureLabel(count, unread) {
    var label = "Hidden (" + count + ")";
    if (unread > 0) label += " \u2022 " + unread + " unread";
    return label;
  }

  return {
    ACTION_WIDTH: ACTION_WIDTH,
    OPEN_THRESHOLD: OPEN_THRESHOLD,
    partition: partition,
    hiddenUnread: hiddenUnread,
    swipeDecision: swipeDecision,
    disclosureLabel: disclosureLabel,
  };
});

/* EOF */
