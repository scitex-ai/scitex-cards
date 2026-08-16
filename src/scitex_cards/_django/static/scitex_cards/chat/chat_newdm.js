/* Start a new message from the sidebar.
 *
 * OPERATOR REQUEST 2026-08-15: the chat view only listed agents who had already
 * DM'd the operator — with the users registry dead on every post-migration
 * host (a per-host sidecar with no DB read path) there was no way to START a
 * conversation with anyone else. The backend now seeds the roster from the
 * cards' owners; this module is the operator's way to use it: type a name,
 * Enter opens the thread (the POST auto-creates one for any peer), and the
 * datalist offers the known names while typing.
 *
 * WHY A DATALIST AND NOT A DROPDOWN: the roster is a suggestion, not a
 * whitelist. Any name the operator types is a valid peer — a thread is created
 * on first message — so free-form typing stays first-class and the datalist
 * only makes the known names easy to pick.
 *
 * SELF-MOUNTING, the way the emoji picker is: the tail below finds its two
 * elements and wires them at load, so the page can be asserted to actually
 * SERVE the input (the markup lives in the template, same rule as the filter
 * row above it). chat.js touches this module at exactly two seams — it hands
 * over the opener (the same openThread + closeDrawer a row click runs) and the
 * polled roster. The template lists this module in its load-order comment BY
 * FUNCTION, never by filename.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script defer src=chat_newdm.js> -> window.ChatNewDm
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
    root.ChatNewDm = api;
  }
  /* Browser only, and the DOM is parsed already because the tag is `defer`.
   * Node tests never reach this line, so the pure helpers above stay
   * exercisable without a browser. */
  if (typeof document !== "undefined" && document.getElementById) {
    api.mount({
      input: document.getElementById("new-dm"),
      datalist: document.getElementById("new-dm-options"),
    });
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Pure, node-testable: the name as it will be sent. Trims, collapses runs
   * of whitespace to one space, null when there is nothing to start with. */
  function normalizeName(value) {
    var name = String(value == null ? "" : value)
      .replace(/\s+/g, " ")
      .trim();
    return name || null;
  }

  /* Pure: roster rows -> the datalist's option values, deduped, order kept.
   * The backend already dedupes; this keeps the DOM honest even if a row ever
   * arrives malformed. */
  function optionNames(agents) {
    var seen = {};
    var out = [];
    for (var i = 0; i < (agents || []).length; i++) {
      var name = normalizeName(agents[i] && agents[i].name);
      if (name && !seen[name]) {
        seen[name] = true;
        out.push(name);
      }
    }
    return out;
  }

  var opener = null; // chat.js hands over the row-click behaviour
  var names = []; // the latest polled roster
  var bound = null; // { input, datalist } once mounted
  var renderedKey = ""; // what the datalist currently shows

  /* Rebuild the datalist ONLY when the roster actually changed: the poll
   * fires every ten seconds, and rewriting options under an open datalist
   * would flicker the operator's suggestion list. */
  function render() {
    if (!bound) return;
    var key = names.join("\n");
    if (key === renderedKey) return;
    renderedKey = key;
    var dl = bound.datalist;
    while (dl.firstChild) dl.removeChild(dl.firstChild);
    var doc = dl.ownerDocument;
    for (var i = 0; i < names.length; i++) {
      var option = doc.createElement("option");
      option.value = names[i];
      dl.appendChild(option);
    }
  }

  function setAgents(agents) {
    names = optionNames(agents);
    render();
  }

  function setOpener(fn) {
    if (typeof fn === "function") opener = fn;
  }

  /* Mount: the caller owns the element lookup (the self-mount tail, or a test
   * with fakes) so this module never guesses at ids — same contract as
   * ChatFilter. Enter starts, Escape clears, and the input is never a form
   * submit: it sits outside the compose form entirely, so Enter must be
   * claimed explicitly. */
  function mount(options) {
    var opts = options || {};
    var input = opts.input;
    var datalist = opts.datalist;
    if (!input || !datalist) return null;
    bound = { input: input, datalist: datalist };
    render();
    function onKey(event) {
      if (event.key === "Enter") {
        event.preventDefault();
        var name = normalizeName(input.value);
        if (!name || typeof opener !== "function") return;
        /* Clear before the call: the thread pane re-renders async, and an
         * input that still holds the name would re-fire the same opener if
         * the operator hits Enter twice. Blurring closes the datalist and
         * moves attention to the conversation that just opened. */
        input.value = "";
        opener(name);
        if (input.blur) input.blur();
      } else if (event.key === "Escape" && input.value) {
        event.preventDefault();
        input.value = "";
      }
    }
    input.addEventListener("keydown", onKey);
    return {
      destroy: function () {
        input.removeEventListener("keydown", onKey);
        bound = null;
        renderedKey = "";
      },
    };
  }

  return {
    normalizeName: normalizeName,
    optionNames: optionNames,
    setAgents: setAgents,
    setOpener: setOpener,
    mount: mount,
  };
});

/* EOF */
