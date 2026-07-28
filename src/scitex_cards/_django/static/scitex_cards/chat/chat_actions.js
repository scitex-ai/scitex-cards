/* Pure logic for the DM message actions — REACT and FORWARD. No DOM, no fetch.
 *
 * Split out of chat.js for the same reason chat_diff.js was: everything here
 * is a decision ("what does this tap mean?", "what body does a forward
 * produce?", "have the reactions changed?"), and a decision that lives in a
 * DOM-touching file is a decision nothing can test. The test suite requires
 * THIS file and exercises the real functions.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_actions.js> before chat.js -> window.ChatActions
 *   - node (tests): require() -> module.exports
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
    root.ChatActions = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* The palette the picker offers — LITERAL unicode, deliberately.
   *
   * Same reasoning as the composer's literal-text paperclip: this board is
   * served over a tunnel to a phone, so an icon font or a sprite sheet that
   * fails to resolve leaves an EMPTY BOX exactly where the affordance was.
   * A literal emoji is rendered by the platform and cannot fail to load.
   *
   * Kept byte-identical to _reactions.REACTION_EMOJI on the Python side; a
   * test pins the two together so the picker can never offer something the
   * server does not expect to see.
   */
  var REACTION_EMOJI = ["👍", "✅", "❤️", "🎉", "🙏", "👀", "🔥", "❌"];

  /* --- forward ---------------------------------------------------------- */

  /* Marker a forwarded body opens with.
   *
   * Mirrors claude-code-telegrammer's `forwardBanner`
   * (ts/lib/forward.ts) — "[forwarded from <name>, <iso>]" — rather than
   * inventing a second convention. The operator reads forwards in both places
   * and should not have to learn two shapes.
   *
   * It lives in the BODY, not in a new record field, for the reason the
   * attachment convention already documents in chat.js: threads.json is the DM
   * store and widening its record is the change that has cost this board data
   * before. The body is the source of truth, so a line IS the metadata — and an
   * older client shows the banner as text rather than showing nothing.
   */
  var FORWARD_RE = /^\[forwarded from .*?\]/;

  function forwardBanner(fromName, ts) {
    var who = String(fromName || "unknown").trim() || "unknown";
    var when = String(ts || "").trim();
    return when
      ? "[forwarded from " + who + ", " + when + "]"
      : "[forwarded from " + who + "]";
  }

  /* Whether `body` already opens with a forward banner. */
  function isForwarded(body) {
    return FORWARD_RE.test(String(body || "").trimStart());
  }

  /* The body a forward of `message` should POST to the target thread.
   *
   * Forwarding an ALREADY-forwarded message keeps the ORIGINAL banner and does
   * not stack a second one — the same rule Telegram applies, and the one that
   * keeps the useful fact (who actually wrote this) at the top instead of
   * burying it under a chain of relayers.
   *
   * Attachment lines are part of the body, so a forwarded image forwards its
   * image for free; nothing here needs to know what an attachment is.
   */
  function forwardBody(message) {
    var body = String((message && message.body) || "");
    if (isForwarded(body)) return body;
    var banner = forwardBanner(message && message.from, message && message.ts);
    return body ? banner + "\n" + body : banner;
  }

  /* --- reactions -------------------------------------------------------- */

  /* The action a tap by `actor` should record, given who has already reacted.
   *
   * Deliberately the same rule as `_reactions.next_action` on the Python side:
   * present -> "remove", absent -> "add". If the two ever disagreed, a tap
   * would toggle one way in the UI and the other way in the store.
   */
  function nextAction(actors, actor) {
    var list = actors || [];
    return list.indexOf(actor) === -1 ? "add" : "remove";
  }

  /* Reactions for one message, as an ordered chip list.
   *
   * `map` is {emoji: [actors]} as the server folds it. Order is the map's own
   * insertion order — i.e. first-reacted-first — so a chip does not jump
   * position when someone else joins it.
   */
  function chipsOf(map, viewer) {
    var out = [];
    if (!map) return out;
    Object.keys(map).forEach(function (emoji) {
      var actors = map[emoji] || [];
      if (!actors.length) return;
      out.push({
        emoji: emoji,
        actors: actors,
        count: actors.length,
        mine: actors.indexOf(viewer) !== -1,
      });
    });
    return out;
  }

  /* A stable string that changes exactly when a message's reactions change.
   *
   * THIS IS WHY IT EXISTS: chat_diff's fingerprint covers id/body/from/ts, so
   * a poll that brings back new reactions and no new message compares EQUAL
   * and plans a "noop" — the pane would never repaint, and a reaction from the
   * agent side would stay invisible until somebody happened to send a message.
   * Folding this into the fingerprint is what makes reactions live.
   */
  function reactionSignature(map) {
    if (!map) return "";
    return Object.keys(map)
      .sort()
      .map(function (emoji) {
        return emoji + ":" + (map[emoji] || []).slice().sort().join(",");
      })
      .join(";");
  }

  /* Human-readable "who reacted", for a chip's tooltip. */
  function actorsLabel(actors) {
    var list = actors || [];
    if (!list.length) return "";
    return list.join(", ");
  }

  return {
    REACTION_EMOJI: REACTION_EMOJI,
    actorsLabel: actorsLabel,
    chipsOf: chipsOf,
    forwardBanner: forwardBanner,
    forwardBody: forwardBody,
    isForwarded: isForwarded,
    nextAction: nextAction,
    reactionSignature: reactionSignature,
  };
});

/* EOF */
