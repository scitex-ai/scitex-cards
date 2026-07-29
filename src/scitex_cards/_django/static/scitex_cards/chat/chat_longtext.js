/* Long DM bodies: bound their HEIGHT on screen, keep every byte, and offer
 * the whole thing as a .txt download.
 *
 * Operator, verbatim (2026-07-28): 「長い文章はテキストファイルに変換して
 * アップロード・ダウンロードできるように、画面を埋め尽くさないように」 — a long
 * body should be available as a text file, and must not fill the screen. Their
 * own thread is the evidence: one pasted element-inspector dump ran to dozens
 * of lines, took the entire viewport, and pushed the actual conversation out
 * of it.
 *
 * NOTHING IS TRUNCATED. The bubble receives the FULL text and the clamp is a
 * CSS `max-height` on that node, so find-in-page, select-all and copy still
 * see every character, and expanding is a class flip rather than a re-fetch.
 * A JS preview built by slicing the string would have been fewer lines and
 * would have destroyed the tail — the store is append-only by operator ruling
 * and the rendering has no business being lossier than the store.
 *
 * WHERE THE NUMBERS COME FROM (they are not round-number guesses):
 *
 *   The operator reads this on a phone. At a 390px viewport the message pane
 *   is ~362px wide, a bubble is 78% of that (~282px), the bubble font is
 *   0.88rem (~14px) at line-height 1.35 (~19px per line), and a UI sans
 *   averages ~0.5em per character — so ~40 characters per rendered line. The
 *   pane itself is roughly viewport minus header, thread title and composer:
 *   ~670px, i.e. ~35 lines. THIRTY-FIVE LINES x FORTY CHARACTERS ~= 1400
 *   CHARACTERS IS EXACTLY "FILLS THE SCREEN".
 *
 *   CLAMP_LINES = 8   ~152px + padding ~= a quarter of that pane, so four
 *                     messages still fit on screen at once.
 *   LONG_CHARS  = 900 ~23 rendered lines on the phone — about 3x the clamp.
 *   LONG_LINES  = 16  2x the clamp, for text that is ALREADY hard-wrapped
 *                     (logs, element dumps, tracebacks) where the character
 *                     count badly under-counts the height.
 *
 *   Both thresholds are >= 2x the clamp on purpose: clamping must always buy
 *   back more screen than the control row underneath it costs. A body between
 *   the clamp and the threshold is left alone rather than decorated for a
 *   saving of a few pixels.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_longtext.js> before chat.js -> window.ChatLongText
 *   - node (tests): require() -> module.exports
 *
 * The pure half (isLong / summaryOf / fileNameFor) is what node exercises;
 * the DOM half touches `document` only when called, so requiring the file
 * outside a browser is safe.
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
    root.ChatLongText = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* See the header for how each of these was derived. CLAMP_LINES is also
   * declared as `--longtext-lines` in chat.html; a test pins the two to the
   * same number so the JS decision and the CSS budget cannot drift. */
  var CLAMP_LINES = 8;
  var LONG_CHARS = 900;
  var LONG_LINES = 16;

  /* Which messages the operator has expanded, keyed by message id.
   *
   * Kept in the MODULE, not in the DOM node, because the thread pane rebuilds
   * every node whenever the polled history diverges from what is on screen.
   * Without this, opening a long message and waiting five seconds would snap
   * it shut again — and it would look like the page had lost the text. */
  var expanded = Object.create(null);

  /* Stable identity of a message record.
   *
   * Deliberately duplicated from ChatDiff.messageKey rather than imported:
   * this file has no dependencies by design (node loads it in isolation), and
   * a load-order dependency between two plain <script> tags is the fragility
   * that split these modules apart in the first place. */
  function messageKey(message) {
    if (!message) return "";
    if (message.id) return String(message.id);
    return String(message.ts || "") + "|" + String(message.from || "");
  }

  function lineCount(text) {
    return String(text || "").split("\n").length;
  }

  /* Whether a body would take an unreasonable share of the screen. */
  function isLong(text) {
    var body = String(text || "");
    return body.length > LONG_CHARS || lineCount(body) > LONG_LINES;
  }

  /* Digit grouping done by hand rather than with toLocaleString: the latter
   * is locale- and runtime-dependent, which would make this untestable. */
  function grouped(count) {
    return String(count).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  /* What the operator needs to decide whether to expand or to download.
   * Characters, not bytes: the count is exact, encoding-independent, and is
   * the unit the text was written in. */
  function summaryOf(text) {
    var body = String(text || "");
    return (
      grouped(lineCount(body)) +
      " lines, " +
      grouped(body.length) +
      " characters"
    );
  }

  /* Download name for one message. Restricted to characters that survive
   * every filesystem the operator might save onto; the message id is what
   * makes two downloads from one thread distinguishable. */
  function fileNameFor(message) {
    var stem = messageKey(message).replace(/[^A-Za-z0-9._-]+/g, "-");
    return "dm-" + (stem || "message") + ".txt";
  }

  /* Hand the full text to the browser as a file.
   *
   * Self-contained: a Blob built from text already in memory, no request. The
   * object URL is minted ON THE CLICK and revoked straight after, rather than
   * being attached to an <a href> when the bubble is built — a thread pane
   * that rebuilds on every divergent poll would otherwise mint a fresh URL per
   * long message per rebuild and never release one, i.e. leak the whole body
   * repeatedly for the life of the page. */
  function downloadText(text, filename) {
    var url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 0);
  }

  function button(label) {
    var node = document.createElement("button");
    node.type = "button";
    node.className = "longtext-btn";
    node.textContent = label;
    return node;
  }

  /* The bubble for one message body, plus its controls when it is long.
   *
   * Returns a DocumentFragment so the controls are a SIBLING of the bubble
   * rather than a child of it: anything inside the bubble would be clipped by
   * the very `overflow: hidden` that does the clamping, i.e. the "Show all"
   * button would itself be hidden by the thing it exists to undo.
   */
  function bubbleFor(text, message) {
    var fragment = document.createDocumentFragment();
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    fragment.appendChild(bubble);
    if (!isLong(text)) return fragment;

    var key = messageKey(message);
    var open = !!expanded[key];
    var summary = summaryOf(text);
    var tools = document.createElement("div");
    tools.className = "longtext-tools";
    var toggle = button("");

    function paint() {
      bubble.classList.toggle("clamped", !open);
      toggle.textContent = open ? "Show less" : "Show all — " + summary;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    toggle.addEventListener("click", function () {
      open = !open;
      expanded[key] = open;
      paint();
    });
    paint();

    var download = button("Download .txt");
    download.addEventListener("click", function () {
      downloadText(text, fileNameFor(message));
    });

    tools.appendChild(toggle);
    tools.appendChild(download);
    fragment.appendChild(tools);
    return fragment;
  }

  return {
    CLAMP_LINES: CLAMP_LINES,
    LONG_CHARS: LONG_CHARS,
    LONG_LINES: LONG_LINES,
    bubbleFor: bubbleFor,
    fileNameFor: fileNameFor,
    isLong: isLong,
    messageKey: messageKey,
    summaryOf: summaryOf,
  };
});

/* EOF */
