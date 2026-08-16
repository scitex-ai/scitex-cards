/* 16-card-helpers.js — the two pure card helpers every other cluster in the
 * scitex-cards GUI (board_v3) depends on: HTML escaping and the owner rule.
 *
 * Extracted VERBATIM from board_v3.html's inline <script> (the C6 + C1
 * clusters) as split PR-2, following 15-dateinfo.js
 * (card scitex-cards-gui-board-v3-template-split-20260717). Pure functions of
 * their arguments — no DOM, no shared globals — so `node` can require() the
 * SHIPPED file and test it for real, which the inline copies never could be.
 * Behaviour is unchanged; only the location moved.
 *
 * Publishes window.STX.cardHelpers.
 *
 * NOTE ON THE TEMPLATE ALIAS. escapeHtml has ~47 call sites in the template
 * AND is read off `window` by already-extracted modules (timeline.js:425,
 * 14-matrix.js:39, 10-agent-avatar.js:36, timelineSelect.js:82). Rewriting 47
 * template-literal call sites would risk a runtime throw at a single missed
 * ${escapeHtml(x)} with no test to catch it, and dropping the window global
 * would silently strip escaping in four other files. So the template keeps a
 * one-line escapeHtml that DELEGATES here. The delegation resolves
 * STX.cardHelpers at CALL time, not at parse time: the inline <script> runs
 * during parsing and this module is `defer`, so a parse-time lookup would
 * read undefined. If this module ever fails to load, the alias throws naming
 * STX.cardHelpers — loud, and it can never render an unescaped string.
 *
 * cardOwner had exactly one call site, so that one was rewritten to call
 * through this module directly and the template copy is gone.
 */
"use strict";

(function (global) {
  // Escape the five HTML-significant characters. Every template literal that
  // interpolates card data goes through this; it is the board's only XSS
  // boundary, which is why it gets real tests here rather than none inline.
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  // Owner SSOT (client mirror of scitex_cards._owner.card_owner) — the
  // SINGLE rule for "who owns this card": `agent` falling back to
  // `assignee`, else null (an owner-less card). Used for the card-render
  // owner line + the unassigned-lane bucketing so an assignee-only card
  // shows its real owner instead of a blank, and an owner-less card lands
  // in a clearly-labeled lane rather than a retired fallback identity.
  function cardOwner(t) {
    if (!t) return null;
    var o = ((t.agent || t.assignee || "") + "").trim();
    return o || null;
  }

  var _api = {
    escapeHtml: escapeHtml,
    cardOwner: cardOwner,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.cardHelpers = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
