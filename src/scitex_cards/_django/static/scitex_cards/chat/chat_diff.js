/* Pure render planning for the DM thread pane — no DOM, no fetch.
 *
 * Split out of chat.js so the decision "what should the pane repaint?"
 * is testable on its own: this file touches no browser API, so the test
 * suite imports THIS file and exercises the real functions rather than a
 * hand-ported copy of them.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_diff.js> before chat.js -> window.ChatDiff
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
    root.ChatDiff = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Stable identity of a message record. `id` is the sidecar's own key
   * (m_<hex>); the ts+from fallback keeps records written before the id
   * field existed from colliding into one another. */
  function messageKey(message) {
    if (!message) return "";
    if (message.id) return String(message.id);
    return String(message.ts || "") + "|" + String(message.from || "");
  }

  /* Everything the pane actually paints for one message. Two records with
   * equal fingerprints render identically, so a bubble already on screen
   * is still correct and can be left alone. Body is included on purpose:
   * an edited message changes its fingerprint without changing the
   * thread's length, which a count-based check cannot see. */
  function messageFingerprint(message) {
    if (!message) return "";
    return [
      messageKey(message),
      String(message.body || ""),
      String(message.from || ""),
      String(message.ts || ""),
    ].join("\u0000");
  }

  /* Decide how to bring the pane from `rendered` to `messages`.
   *
   *   rendered  array of fingerprints currently in the DOM, in order
   *   messages  the server's chronological message list
   *
   * Returns {mode, fingerprints, added}:
   *   "noop"     already correct — leave the DOM (and the user's text
   *              selection) untouched
   *   "append"   `rendered` is a prefix of the new list: only the tail is
   *              new, so append `added` and touch nothing else
   *   "rebuild"  the history diverged (edit, deletion, reorder, thread
   *              switch) — the prefix no longer matches, so repaint all
   *
   * Append is the overwhelmingly common case: a DM thread grows at the
   * end. Rebuild is the honest fallback rather than a guess.
   */
  function planRender(rendered, messages) {
    var current = rendered || [];
    var list = messages || [];
    var fingerprints = list.map(messageFingerprint);

    if (current.length > fingerprints.length) {
      return { mode: "rebuild", fingerprints: fingerprints, added: [] };
    }
    for (var i = 0; i < current.length; i += 1) {
      if (current[i] !== fingerprints[i]) {
        return { mode: "rebuild", fingerprints: fingerprints, added: [] };
      }
    }
    if (current.length === fingerprints.length) {
      return { mode: "noop", fingerprints: fingerprints, added: [] };
    }
    return {
      mode: "append",
      fingerprints: fingerprints,
      added: list.slice(current.length),
    };
  }

  /* Whether the pane should follow new messages down.
   *
   * Telegram's rule, and the one the operator expects: stick to the
   * newest message only if you were already at the bottom. Scrolled up
   * reading history means a poll must NOT yank you back down.
   * An empty/short pane is "at bottom", so a freshly opened thread lands
   * on the newest message without a special case.
   */
  function shouldStickToBottom(scrollTop, scrollHeight, clientHeight, threshold) {
    var slack = typeof threshold === "number" ? threshold : 0;
    return scrollHeight - scrollTop - clientHeight <= slack;
  }

  /* Format a stored UTC timestamp on the VIEWER'S OWN clock.
   *
   * This used to string-slice the ISO stamp ("…T20:39" -> "20:39"), printing
   * UTC digits as though they were local. The operator, in Japan, read a
   * 20:39Z stamp as an evening message and asked whether the board was on US
   * time; it was 05:39 their morning. Slicing a timestamp is not formatting
   * one — it silently asserts the reader's clock is UTC.
   *
   * The store writes UTC. A bare "…THH:MM:SS" carrying no Z and no offset is
   * parsed as LOCAL by JS, which would shift exactly those stamps by the
   * viewer's offset, so pin the value to UTC before parsing rather than
   * trusting the shape. An unparseable value is returned verbatim: showing the
   * raw string is honest, showing a confidently wrong time is not.
   */
  function shortTs(ts) {
    if (!ts) return "";
    var raw = String(ts);
    var iso = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : raw + "Z";
    var parsed = new Date(iso);
    if (isNaN(parsed.getTime())) return raw;
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    return (
      pad(parsed.getMonth() + 1) +
      "-" +
      pad(parsed.getDate()) +
      " " +
      pad(parsed.getHours()) +
      ":" +
      pad(parsed.getMinutes())
    );
  }

  return {
    messageKey: messageKey,
    messageFingerprint: messageFingerprint,
    planRender: planRender,
    shortTs: shortTs,
    shouldStickToBottom: shouldStickToBottom,
  };
});

/* EOF */
