/* Pure view decisions for the "My Cards" phone page — no DOM, no fetch.
 *
 * Split out of me.js for the reason chat_diff.js was split out of chat.js:
 * extraction alone does not buy testability, DOM-FREEDOM does. A module that
 * touches `document` at import time cannot be require()d by node even after it
 * has its own file, so the decisions worth testing — what sections to draw,
 * whether a deadline is late, what to say when the board will not tell you who
 * you are — live HERE, where the test exercises the REAL shipped file instead
 * of a hand-ported copy of it.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=me_view.js> before me.js -> window.MeView
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
    root.MeView = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* The sections the phone draws, in the order it draws them: what I am
   * doing, what is stuck, what is waiting, then what is closed. Mirrors the
   * server's _STATUS_ORDER deliberately — the endpoint already sorts, and a
   * client that regrouped by its own order would make the list disagree with
   * its own counts. */
  var SECTIONS = [
    { key: "in_progress", label: "In progress" },
    { key: "blocked", label: "Blocked" },
    { key: "deferred", label: "Waiting" },
    { key: "goal", label: "Goals" },
    { key: "done", label: "Done" },
    { key: "failed", label: "Failed" },
    { key: "cancelled", label: "Cancelled" },
  ];

  /* Group the endpoint's flat card list into the sections above.
   *
   * EMPTY SECTIONS ARE DROPPED, not rendered as empty headings: on a 375pt
   * screen seven headings with nothing under them push the one card that
   * matters below the fold. A status the client does not know about still
   * gets a section — labelled with the raw status — because a card the page
   * cannot categorise must remain visible to its owner rather than vanish. */
  function planSections(cards) {
    var list = Array.isArray(cards) ? cards : [];
    var byStatus = {};
    var order = [];
    var i;
    for (i = 0; i < list.length; i++) {
      var status = (list[i] && list[i].status) || "unknown";
      if (!byStatus[status]) {
        byStatus[status] = [];
        order.push(status);
      }
      byStatus[status].push(list[i]);
    }
    var planned = [];
    for (i = 0; i < SECTIONS.length; i++) {
      var known = SECTIONS[i];
      if (byStatus[known.key]) {
        planned.push({
          key: known.key,
          label: known.label,
          cards: byStatus[known.key],
        });
        byStatus[known.key] = null;
      }
    }
    for (i = 0; i < order.length; i++) {
      if (byStatus[order[i]]) {
        planned.push({
          key: order[i],
          label: order[i],
          cards: byStatus[order[i]],
        });
      }
    }
    return planned;
  }

  /* Milliseconds in the units the page speaks. */
  var MINUTE = 60 * 1000;
  var HOUR = 60 * MINUTE;
  var DAY = 24 * HOUR;

  function parseTime(value) {
    if (!value) return null;
    var parsed = Date.parse(value);
    return isNaN(parsed) ? null : parsed;
  }

  /* "3h ago" — coarse on purpose.
   *
   * A phone glance wants an ORDER OF MAGNITUDE, not a timestamp; the exact
   * value is one tap away on the card. Returns "" for an unparseable or
   * absent stamp so the caller renders nothing rather than "NaN ago", which
   * is how a missing field usually reaches a user. */
  function relativeTime(value, now) {
    var then = parseTime(value);
    if (then === null) return "";
    var reference = typeof now === "number" ? now : Date.now();
    var delta = reference - then;
    if (delta < 0) return "just now";
    if (delta < MINUTE) return "just now";
    if (delta < HOUR) return Math.floor(delta / MINUTE) + "m ago";
    if (delta < DAY) return Math.floor(delta / HOUR) + "h ago";
    var days = Math.floor(delta / DAY);
    if (days < 30) return days + "d ago";
    var months = Math.floor(days / 30);
    if (months < 12) return months + "mo ago";
    return Math.floor(days / 365) + "y ago";
  }

  /* How a card's deadline should read: "" (none), "later", "soon" (within a
   * day), "today", or "overdue".
   *
   * OVERDUE IS COMPUTED FROM THE DATE, not read from a flag, because nothing
   * in this system fires when a deadline passes — the store's own docs are
   * explicit that a deadline is a VIEW, never a notifier, and that the
   * overdue filter is pull-only. So the page that shows the deadline is the
   * thing that has to notice. */
  function deadlineState(card, now) {
    var when = parseTime(card && (card.deadline_next || card.deadline));
    if (when === null) return "";
    var reference = typeof now === "number" ? now : Date.now();
    var delta = when - reference;
    if (delta < 0) return "overdue";
    if (delta < DAY) return "today";
    if (delta < 3 * DAY) return "soon";
    return "later";
  }

  /* What to tell a person the board will not identify.
   *
   * The two cases are genuinely different and must not collapse into one
   * "access denied": an UNLINKED visitor is signed in and needs their address
   * connected to a board identity, while an ANONYMOUS one is looking at a
   * board that has no per-user login at all. Telling the second person to
   * "link your account" sends them looking for a screen that does not exist.
   *
   * The server's own `detail` is preferred when present — it is the half that
   * knows the deployment — and these are the fallbacks for when it is not. */
  function refusalMessage(body) {
    var reason = (body && body.reason) || "";
    var detail = (body && body.detail) || "";
    if (reason === "unlinked-email") {
      return {
        title: "Account not linked yet",
        detail:
          detail ||
          "You are signed in, but this address is not connected to a board " +
            "identity yet, so there is no way to tell which cards are yours.",
        email: (body && body.email) || "",
      };
    }
    return {
      title: "This board does not know who you are",
      detail:
        detail ||
        "It has no per-user login configured, so it cannot show one " +
          "person's cards rather than everyone's.",
      email: (body && body.email) || "",
    };
  }

  /* The one-line summary above the list: "3 open — 1 blocked".
   *
   * Built from the payload's OWN counts rather than recounted here, so the
   * header cannot disagree with the list it sits above. */
  function summarise(payload) {
    var counts = (payload && payload.counts) || {};
    var total =
      payload && typeof payload.total === "number" ? payload.total : 0;
    if (!total) return "Nothing on your plate";
    var parts = [total + (total === 1 ? " card" : " cards")];
    if (counts.blocked) parts.push(counts.blocked + " blocked");
    return parts.join(" — ");
  }

  return {
    SECTIONS: SECTIONS,
    deadlineState: deadlineState,
    planSections: planSections,
    refusalMessage: refusalMessage,
    relativeTime: relativeTime,
    summarise: summarise,
  };
});

/* EOF */
