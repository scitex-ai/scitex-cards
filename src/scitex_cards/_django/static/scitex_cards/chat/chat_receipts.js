/* The delivery indicator on a DM bubble: three dots on a line, filling left to
 * right as a message advances. Nothing else.
 *
 * PRESENTATION IS THE OPERATOR'S, 2026-07-29 09:06: 「三つドットとラインがあって、
 * 埋まっていく感じだときれいかもです。マウスオーバーで意味が分かるみたいな。なるべく
 * 画面を綺麗にしたいので」 — three dots joined by a line, meaning on hover, and above
 * all QUIET: this sits on every message in the thread, so if it draws the eye
 * more than the message text it is wrong.
 *
 * IT IS DELIBERATELY NOT cct's EMOJI. The earlier instruction was to copy ⚡/👀
 * verbatim; the operator withdrew it at 09:03 — 「そうしないとイナズマと目のマークを
 * いちいち説明しないといけないので、初見者には優しくないなと」 (the symbols have to be
 * explained to a first-time reader). THE MEANINGS ARE STILL cct's, only the
 * presentation changed:
 *
 *   sent    cct stage 1 (RECEIPT_DELIVERED_EMOJI) — durable in the database.
 *   queued  the recipient's inbox holds it.
 *   read    cct stage 2 as ORIGINALLY specified (#14) — the RECIPIENT
 *           confirmed it. cct itself moved this signal off 👀 only because it
 *           had no recipient-sourced evidence to hang it on; cards does.
 *
 * THE ONE RULE: A DOT IS NEVER FILLED BY INFERENCE. "read" is filled only by a
 * dm_receipts row written by the reader itself. A transport call returning is
 * exactly what lied to the operator for weeks — every one of their DMs was in
 * the queue and not one reached a session. A dot filled on "we handed it to
 * something" would have shown a full track throughout the entire outage, i.e.
 * the feature would have concealed the bug it exists to expose.
 *
 * THREE DOT STATES, and the third is the point:
 *
 *   on       filled disc     — this step is confirmed
 *   off      hollow ring     — this step has NOT happened yet (and we would
 *                              know if it had)
 *   unknown  dashed ring     — we cannot see this step at all
 *
 * "unknown" must never be readable as "on", and must not be readable as "off"
 * either: "hasn't happened" is a claim, and we are not entitled to it. This is
 * the same rule as everywhere else today — unknown is a third answer, not the
 * safe pole of a boolean.
 *
 * FILL AND BORDER-STYLE CARRY THE STATE, NOT COLOUR. Colour-only state fails
 * for colour-blind readers and in high-contrast modes, so a disc, a ring and a
 * dashed ring differ in shape before they differ in shade.
 *
 * MOBILE IS THE OPERATOR'S #1 PRIORITY FOR THIS PAGE, AND HOVER DOES NOT EXIST
 * THERE. A meaning reachable only by mouse is unreachable in most of their
 * usage, so: the track carries a real accessible name (title + aria-label), it
 * is focusable, and a TAP reveals the same wording as inline text rather than
 * falling through to nothing.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_receipts.js> before chat.js -> window.ChatReceipts
 *   - node (tests): require() -> module.exports
 *
 * The decision half (trackFor) is DOM-free so the tests drive the real
 * function; only render() touches the document.
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
    root.ChatReceipts = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DOT_ON = "on";
  var DOT_OFF = "off";
  var DOT_UNKNOWN = "unknown";

  var STATE_PENDING = "pending";
  var STATE_RECEIVED = "received";
  var STATE_UNKNOWABLE = "unknowable";

  var DEFAULT_VIEWER = "operator";

  /* QUEUED IS NOT IMPLEMENTED, AND IS THEREFORE DRAWN AS UNKNOWN — never as
   * off, and never as on. The inbox notification does not carry the message id;
   * the only join available is (thread, ts, actor), which is ALSO the inbox's
   * dedupe key while DM stamps are second-resolution, so it is many-to-one BY
   * CONSTRUCTION. It was measured collapsing two distinct live messages onto one
   * notification. Filling this dot from that join would put a mark on a message
   * nothing was ever delivered for, which is the precise failure the indicator
   * exists to detect. Carrying msg_id into _inbox.enqueue is what makes this dot
   * honest; until then it stays dashed and says so on hover. */
  var QUEUED_IS_KEYABLE = false;

  var LABELS = {
    sent: "sent - durable in the database",
    queued: "queued - not observable yet (the notification carries no message id)",
    readOn: "read - confirmed by ",
    readOff: "read - the recipient has NOT confirmed receipt",
    readUnknown: "read - no recipient can confirm this message, so it cannot be told",
  };

  /* Same identity rule as chat_diff.messageKey. Duplicated rather than
   * imported for the same reason chat_diff duplicates its signature helpers:
   * these files are plain <script> tags with no load-order dependency between
   * them, and a test pins the two to the same output. */
  function messageKey(message) {
    if (!message) return "";
    if (message.id) return String(message.id);
    return String(message.ts || "") + "|" + String(message.from || "");
  }

  /* Stable string for one message's receipt entry; "" when there is none.
   *
   * This is what lets a confirmation REPAINT a bubble. A receipt arriving on
   * its own changes no message field and no reaction, so a fingerprint built
   * without it compares equal, planRender answers "noop", and the last dot
   * would never fill until an unrelated message happened to land — the bug
   * would hide exactly the event the indicator is for. */
  function receiptSignature(entry) {
    if (!entry) return "";
    var readers = entry.readers || [];
    return String(entry.state || "") + ":" + readers.slice().sort().join(",");
  }

  function normaliseState(entry) {
    var state = entry && entry.state ? String(entry.state) : STATE_UNKNOWABLE;
    if (
      state !== STATE_PENDING &&
      state !== STATE_RECEIVED &&
      state !== STATE_UNKNOWABLE
    ) {
      // An unrecognised state from a newer server must fail toward "cannot
      // tell", never toward a filled dot.
      return STATE_UNKNOWABLE;
    }
    return state;
  }

  function readDot(state) {
    if (state === STATE_RECEIVED) return DOT_ON;
    if (state === STATE_PENDING) return DOT_OFF;
    return DOT_UNKNOWN;
  }

  function readLabel(state, readers) {
    if (state === STATE_RECEIVED) {
      return LABELS.readOn + ((readers || []).join(", ") || "the recipient");
    }
    if (state === STATE_PENDING) return LABELS.readOff;
    return LABELS.readUnknown;
  }

  /* The three steps to paint for one message, or null to paint nothing.
   *
   *   message   one DM record from the thread endpoint
   *   receipts  the OPTIONAL {message_id: {state, readers}} map served
   *             alongside the messages
   *   viewer    whose own messages carry the track (default "operator")
   *
   * Returns {state, steps: [{name, dot, label}], summary} or null.
   *
   * null in two cases, both honest: the bubble is not the viewer's own (a track
   * on an incoming message would only restate that the reader is reading), or
   * the server sent no receipts map at all (an older server, or the feature
   * off — inventing a position from its absence is the guess this feature
   * forbids).
   *
   * A map that IS present but has no entry for this id yields "unknowable",
   * never "received": the server enumerates every live message, so a gap means
   * we genuinely do not know. */
  function trackFor(message, receipts, viewer) {
    if (!message || !receipts) return null;
    if (String(message.from || "") !== (viewer || DEFAULT_VIEWER)) return null;
    var entry = receipts[messageKey(message)];
    var state = normaliseState(entry);
    var readers = (entry && entry.readers) || [];
    // NO ENTRY MEANS THE WHOLE TRACK IS INDETERMINATE, "sent" INCLUDED.
    //
    // It is tempting to fill "sent" unconditionally on the grounds that we are
    // rendering the message, so it must be stored. MEASURED ON THE LIVE STORE
    // AND FALSE: the thread endpoint returns the UNION of the pre-migration
    // threads.json sidecar and dm_messages, and 57 of the operator's 137
    // messages in one thread exist ONLY in the sidecar. Those have no row in
    // the store the receipts subsystem reads, so "durable" is precisely the
    // claim we cannot make about them — filling the dot would assert
    // durability for the one population that lacks it.
    var known = Boolean(entry);
    var steps = [
      { name: "sent", dot: known ? DOT_ON : DOT_UNKNOWN, label: LABELS.sent },
      {
        name: "queued",
        dot: QUEUED_IS_KEYABLE && known ? DOT_OFF : DOT_UNKNOWN,
        label: LABELS.queued,
      },
      { name: "read", dot: readDot(state), label: readLabel(state, readers) },
    ];
    return {
      state: state,
      steps: steps,
      summary: steps
        .map(function (s) {
          return s.label;
        })
        .join(" / "),
    };
  }

  function dotNode(step) {
    var dot = document.createElement("span");
    dot.className = "rc-dot rc-" + step.dot;
    return dot;
  }

  /* Append the indicator to a message's meta line, if it has earned one.
   *
   * INTO THE META LINE, not beside the bubble: that row already exists on every
   * message, so the track costs no vertical space and cannot push the timestamp
   * onto a second line on a narrow screen. */
  function render(metaNode, message, receipts, viewer) {
    if (!metaNode || typeof document === "undefined") return null;
    var track = trackFor(message, receipts, viewer);
    if (!track) return null;
    var wrap = document.createElement("span");
    wrap.className = "receipt receipt-" + track.state;
    wrap.setAttribute("title", track.summary);
    wrap.setAttribute("aria-label", track.summary);
    wrap.setAttribute("role", "img");
    // Focusable so the meaning is reachable by keyboard and by screen reader,
    // not only by a mouse the operator does not have on their phone.
    wrap.setAttribute("tabindex", "0");
    track.steps.forEach(function (step, index) {
      if (index) wrap.appendChild(document.createElement("i")); // the line
      wrap.appendChild(dotNode(step));
    });
    var label = document.createElement("span");
    label.className = "rc-label";
    label.textContent = track.summary;
    wrap.appendChild(label);
    // THE MOBILE ANSWER, stated rather than left to fall through: hover does
    // not exist on a phone, so a tap toggles the same wording as inline text.
    wrap.addEventListener("click", function () {
      wrap.classList.toggle("rc-open");
    });
    metaNode.appendChild(wrap);
    return wrap;
  }

  return {
    DOT_ON: DOT_ON,
    DOT_OFF: DOT_OFF,
    DOT_UNKNOWN: DOT_UNKNOWN,
    STATE_PENDING: STATE_PENDING,
    STATE_RECEIVED: STATE_RECEIVED,
    STATE_UNKNOWABLE: STATE_UNKNOWABLE,
    messageKey: messageKey,
    receiptSignature: receiptSignature,
    render: render,
    trackFor: trackFor,
  };
});

/* EOF */
