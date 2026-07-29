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
  var REACTION_EMOJI = [
    "⭕",
    "❌",
    "❓",
    "👍",
    "❤️",
    "🎉",
    "✅",
    "🙏",
    "👀",
    "🔥",
  ];

  /* The QUICK row — the one-tap reactions that sit directly above the action
   * list, exactly as the operator sketched it.
   *
   * MUST BE A SUBSET of REACTION_EMOJI (a test pins that), because the row and
   * the chevron's fuller picker are two views of ONE palette. A row emoji the
   * picker did not know about would be a second, drifting palette.
   *
   * The first three are the operator's own request, in their words —
   * 「〇、×、？ がいい」. They are also the whole operator↔agent decision
   * vocabulary: approve, reject, query. They lead the row because they are what
   * gets tapped; the warm three follow.
   *
   * 👎 IS DELIBERATELY ABSENT, HERE AND FROM REACTION_EMOJI. The operator's
   * words: 「親指の下向きのやつはあまり好きじゃない、下品」. ❌ already says
   * "no" without the gesture. A test asserts its absence, so re-adding it as a
   * "sensible default" fails CI rather than shipping.
   *
   * Six fits one 44px row plus the chevron inside a 375pt phone screen without
   * wrapping; padding it to fill the width would only add taps nobody wants.
   */
  var QUICK_REACTION_EMOJI = ["⭕", "❌", "❓", "👍", "❤️", "🎉"];

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

  /* `toName` is the ORIGINAL RECIPIENT, and it is optional.
   *
   * The operator asked for it in email terms — 「元の送信者と元の受信者って
   * いうのが誰か」 — because "forwarded from scitex-dev" alone does not say
   * whether they were reading their own thread or somebody else's. Rendered
   * "[forwarded from <a> to <b>, <ts>]", which FORWARD_RE still matches, so a
   * banner written by the older two-part form (and by claude-code-telegrammer,
   * which does not know about `to`) is still recognised as a banner and still
   * refuses to stack. Omitted rather than guessed when the caller cannot say
   * who the recipient was: a wrong name here is worse than a missing one. */
  function forwardBanner(fromName, ts, toName) {
    var who = String(fromName || "unknown").trim() || "unknown";
    var when = String(ts || "").trim();
    var to = String(toName || "").trim();
    var head = to ? "forwarded from " + who + " to " + to : "forwarded from " + who;
    return when ? "[" + head + ", " + when + "]" : "[" + head + "]";
  }

  /* Who a message in the `peer` thread was originally sent TO.
   *
   * A DM thread has exactly two ends, so the recipient is whichever end did
   * not write it. Derived rather than stored because the record carries only
   * `from` — and deriving it keeps this correct for messages written long
   * before the banner grew a `to`.
   */
  function forwardOriginalTo(message, peer, viewer) {
    var from = String((message && message.from) || "");
    if (!from) return "";
    return from === String(viewer) ? String(peer || "") : String(viewer || "");
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
  function forwardBody(message, toName) {
    var body = String((message && message.body) || "");
    if (isForwarded(body)) return body;
    var banner = forwardBanner(
      message && message.from,
      message && message.ts,
      toName,
    );
    return body ? banner + "\n" + body : banner;
  }

  /* --- marquee geometry ------------------------------------------------- */

  /* The rectangle spanned by two points, in any drag direction.
   *
   * Normalised to left/top/right/bottom so a drag UP-AND-LEFT describes the
   * same box as the same drag down-and-right — without this, three of the four
   * drag directions select nothing, which is the classic marquee bug.
   */
  function marqueeRect(from, to) {
    var ax = (from && from.x) || 0;
    var ay = (from && from.y) || 0;
    var bx = (to && to.x) || 0;
    var by = (to && to.y) || 0;
    return {
      left: Math.min(ax, bx),
      top: Math.min(ay, by),
      right: Math.max(ax, bx),
      bottom: Math.max(ay, by),
    };
  }

  /* Whether two rectangles share any area.
   *
   * INTERSECTION, not containment: a marquee dragged across a long message
   * must catch it even though the box never covers the whole bubble. Requiring
   * containment would make tall messages unselectable, which on this board is
   * most of them.
   */
  function rectsOverlap(a, b) {
    if (!a || !b) return false;
    return !(
      a.right < b.left ||
      a.left > b.right ||
      a.bottom < b.top ||
      a.top > b.bottom
    );
  }

  /* The ids of `boxes` the rectangle touches, in the order boxes came in.
   *
   * Callers pass boxes in thread order, so the result is in thread order too —
   * the same rule `selectedRecords` follows, and for the same reason: a
   * forward must read in the order the conversation happened.
   */
  function idsWithin(boxes, rect) {
    return (boxes || [])
      .filter(function (box) {
        return box && rectsOverlap(rect, box.rect);
      })
      .map(function (box) {
        return box.id;
      });
  }

  /* Union of two id lists, order-preserving and duplicate-free.
   *
   * A second marquee ADDS to the selection rather than replacing it, so the
   * operator can gather messages from several places in a long thread without
   * one careful drag having to catch them all.
   */
  function mergeIds(ids, extra) {
    var out = (ids || []).slice();
    (extra || []).forEach(function (id) {
      if (out.indexOf(id) === -1) out.push(id);
    });
    return out;
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

  /* --- selection -------------------------------------------------------- */

  /* Add or drop `id`, returning a NEW list — the caller never mutates in place.
   *
   * A selection is an ordered list of message IDS rather than a set of DOM
   * nodes for the same reason a reaction addresses an id: the thread repaints
   * every ~5s, and a selection held as nodes would silently empty itself the
   * first time a poll rebuilt the pane. Ids survive a repaint; nodes do not.
   */
  function toggleSelection(ids, id) {
    var list = (ids || []).slice();
    var at = list.indexOf(id);
    if (at === -1) list.push(id);
    else list.splice(at, 1);
    return list;
  }

  /* The selection bar's count. */
  function selectionLabel(count) {
    return (count || 0) + " selected";
  }

  /* The selected records in THREAD order, not in tap order.
   *
   * Tap order is the order the operator happened to touch things in; a bulk
   * copy or a bulk forward that came out in that order would scramble a
   * conversation. Ordering by the message list restores the reading order, and
   * an id no longer in the thread simply drops.
   */
  function selectedRecords(messages, ids) {
    var wanted = ids || [];
    return (messages || []).filter(function (m) {
      return m && wanted.indexOf(String(m.id)) !== -1;
    });
  }

  /* Join several message texts into one clipboard payload.
   *
   * A blank line between messages, and empty texts dropped — an attachment-only
   * message has no text to contribute and must not leave a hole in the middle
   * of the paste.
   */
  function joinTexts(texts) {
    return (texts || [])
      .map(function (t) {
        return String(t == null ? "" : t).trim();
      })
      .filter(Boolean)
      .join("\n\n");
  }

  return {
    QUICK_REACTION_EMOJI: QUICK_REACTION_EMOJI,
    REACTION_EMOJI: REACTION_EMOJI,
    actorsLabel: actorsLabel,
    chipsOf: chipsOf,
    forwardBanner: forwardBanner,
    forwardBody: forwardBody,
    forwardOriginalTo: forwardOriginalTo,
    idsWithin: idsWithin,
    isForwarded: isForwarded,
    joinTexts: joinTexts,
    marqueeRect: marqueeRect,
    mergeIds: mergeIds,
    nextAction: nextAction,
    rectsOverlap: rectsOverlap,
    reactionSignature: reactionSignature,
    selectedRecords: selectedRecords,
    selectionLabel: selectionLabel,
    toggleSelection: toggleSelection,
  };
});

/* EOF */
