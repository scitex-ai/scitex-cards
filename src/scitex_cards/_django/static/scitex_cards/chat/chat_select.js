/* Multi-select mode for the DM chat pane — the menu's "Select" action.
 *
 * Requested by the operator directly (「select はいる」). It exists ONLY
 * because there are real things to do with a selection: this module ships two
 * destinations, Copy and Forward, and both are built on paths that already
 * work — the clipboard the single Copy uses, and the same POST the single
 * Forward uses. A mode that selects things and can then do nothing with them
 * would be a dead control with extra steps.
 *
 * WHAT A SELECTION IS: an ordered list of message IDS, never a list of DOM
 * nodes. The thread repaints every ~5s and a poll may rebuild the pane
 * wholesale, so a selection held as nodes would silently empty itself the
 * first time that happened. Ids survive a repaint; `decorate` re-applies the
 * highlight to each freshly painted node.
 *
 * Mounted by chat_menu.js, which already owns every seam this needs. Nothing
 * here reaches back into chat.js.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  var actions = root.ChatActions;

  function mount(host) {
    var $bar = document.getElementById("select-bar");
    var $count = document.getElementById("sb-count");
    var $copy = document.getElementById("sb-copy");
    var $forward = document.getElementById("sb-forward");
    var $cancel = document.getElementById("sb-cancel");
    var $messages = host.messagesEl;
    if (!$bar || !$messages) return null;

    var active = false;
    var ids = [];
    /* The KEYBOARD CURSOR: which message the arrow keys are "on".
     *
     * Held as an id, not an index, for the reason the selection is: the thread
     * repaints every ~5s and an index would quietly come to mean a different
     * message. null means "no cursor yet".
     */
    var cursorId = null;

    function msgNodes() {
      return Array.prototype.slice.call(
        $messages.querySelectorAll(".msg[data-msg-id]"),
      );
    }

    function idOf(node) {
      return node ? node.getAttribute("data-msg-id") : null;
    }

    function paint() {
      msgNodes().forEach(function (node) {
        var on = ids.indexOf(idOf(node)) !== -1;
        node.classList.toggle("selected", on);
        node.classList.toggle("cursor", cursorId !== null && idOf(node) === cursorId);
      });
      $count.textContent = actions.selectionLabel(ids.length);
      // An action button that cannot act says so, rather than failing on tap.
      var none = ids.length === 0;
      if ($copy) $copy.disabled = none;
      if ($forward) $forward.disabled = none;
    }

    /* Re-apply the highlight to a node the thread pane has just repainted.
     *
     * chat_menu calls this from its per-message paint hook. Without it the
     * selection would LOOK cleared every 5s while still being held, which is
     * the worst of both: the operator re-taps a message that was already in
     * the set and quietly removes it. */
    function decorate(wrap, message) {
      if (!active || !message) return;
      if (ids.indexOf(String(message.id)) !== -1)
        wrap.classList.add("selected");
    }

    /* Turn selection mode on and hold exactly `seedIds`.
     *
     * The one place `active` becomes true, so every entry point — the menu's
     * Select item, a marquee drag, the keyboard — lands in the same state
     * rather than each setting up its own half of it.
     */
    function enterWith(seedIds) {
      active = true;
      ids = (seedIds || []).slice();
      $messages.classList.add("selecting");
      $bar.classList.add("open");
      paint();
    }

    function enter(node) {
      // Seed with the message the menu was opened on: the operator asked to
      // select THAT one, so an empty selection would throw away the tap that
      // got them here.
      var seed = idOf(node);
      enterWith(seed ? [seed] : []);
    }

    /* Add `extra` to the selection, entering selection mode if needed.
     *
     * The marquee's destination. It ADDS rather than replaces so a second drag
     * gathers more messages instead of discarding the first drag's catch — a
     * long thread rarely puts everything the operator wants inside one
     * rectangle. An empty drag is ignored rather than treated as "clear":
     * a rectangle that caught nothing is a miss, not an instruction.
     */
    function addMany(extra) {
      if (!extra || !extra.length) return;
      if (!active) enterWith(extra);
      else {
        ids = actions.mergeIds(ids, extra);
        paint();
      }
    }

    /* Toggle one node, for gestures that mean "select this" outside the tap
     * path — currently the right-click that chat_menu redirects here while
     * selection mode is on. */
    function toggleAt(node) {
      if (!active || !idOf(node)) return;
      ids = actions.toggleSelection(ids, idOf(node));
      paint();
    }

    function exit() {
      if (!active) return;
      active = false;
      ids = [];
      $messages.classList.remove("selecting");
      $bar.classList.remove("open");
      msgNodes().forEach(function (node) {
        node.classList.remove("selected");
      });
      // The cursor ID survives on purpose: leaving selection mode is not
      // leaving the thread, and dropping it would send the next arrow press
      // back to the bottom instead of where the operator was reading.
    }

    function isActive() {
      return active;
    }

    /* CAPTURE phase, deliberately.
     *
     * In selection mode the whole message IS the target, so a tap must not
     * also do what that spot normally does — follow an attachment link, or
     * toggle the reaction chip that happens to be under the finger. Those
     * handlers sit on descendants, so only a capture-phase listener runs
     * before them, and stopPropagation then keeps the event from ever
     * reaching them. */
    $messages.addEventListener(
      "click",
      function (event) {
        if (!active) return;
        var node = event.target.closest ? event.target.closest(".msg") : null;
        if (!node || !idOf(node)) return;
        event.preventDefault();
        event.stopPropagation();
        ids = actions.toggleSelection(ids, idOf(node));
        paint();
      },
      true,
    );

    // ---- keyboard --------------------------------------------------------

    /* A SELECTION THAT ONLY A MOUSE CAN MAKE IS NOT FINISHED.
     *
     * The marquee is pointer-only by design and the menu's Select item needs a
     * right-click or a long-press, which between them leave a keyboard with no
     * way in at all. This is that way in, and it is the standard listbox
     * shape rather than an invention: focus the thread, arrow to a message,
     * Space to take it, Shift+Arrow to run a range, Ctrl+A for everything.
     *
     * The container is what takes focus — one tab stop. Making each of ~50
     * messages tabbable would bury every control after the thread behind fifty
     * presses of Tab, which is a worse accessibility outcome than none.
     */
    $messages.setAttribute("tabindex", "0");
    $messages.setAttribute("role", "listbox");
    $messages.setAttribute("aria-multiselectable", "true");
    $messages.setAttribute("aria-label", "Messages");

    function cursorIndex() {
      var nodes = msgNodes();
      for (var i = 0; i < nodes.length; i += 1)
        if (idOf(nodes[i]) === cursorId) return i;
      return -1;
    }

    /* Move the cursor by `step`, clamped, and scroll it into view.
     *
     * With no cursor yet the first press lands on the LAST message rather than
     * the first: the thread is opened scrolled to the bottom, so the newest
     * message is what the operator is looking at, and starting the cursor
     * hundreds of messages above the viewport would read as nothing happening.
     */
    function moveCursor(step) {
      var nodes = msgNodes();
      if (!nodes.length) return null;
      var at = cursorIndex();
      var next =
        at === -1
          ? step < 0
            ? nodes.length - 1
            : nodes.length - 1
          : Math.min(nodes.length - 1, Math.max(0, at + step));
      cursorId = idOf(nodes[next]);
      if (nodes[next].scrollIntoView)
        nodes[next].scrollIntoView({ block: "nearest" });
      paint();
      return cursorId;
    }

    $messages.addEventListener("keydown", function (event) {
      var key = event.key;
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        var from = cursorId;
        var to = moveCursor(key === "ArrowDown" ? 1 : -1);
        // Shift EXTENDS: both ends of the step join the selection, so holding
        // Shift and running the arrow sweeps a range the way it does in every
        // other list.
        if (event.shiftKey && to) {
          var span = from && from !== to ? [from, to] : [to];
          addMany(span);
        }
        return;
      }
      if (key === " " || key === "Enter") {
        if (!cursorId) return;
        event.preventDefault();
        if (!active) enterWith([cursorId]);
        else toggleAt(nodeById(cursorId));
        return;
      }
      // Select-all is claimed ONLY inside selection mode. Outside it, Ctrl+A
      // still means "select the page's text", which is what a reader wants.
      if ((event.ctrlKey || event.metaKey) && (key === "a" || key === "A")) {
        if (!active) return;
        event.preventDefault();
        ids = msgNodes().map(idOf);
        paint();
      }
    });

    function nodeById(id) {
      var found = null;
      msgNodes().forEach(function (node) {
        if (idOf(node) === id) found = node;
      });
      return found;
    }

    // ---- bulk copy -------------------------------------------------------

    /* The bubble texts of the selected messages, in DOM order.
     *
     * Read off the DOM rather than off the records for one reason: it is
     * EXACTLY what the single-message Copy copies. Both items say "Copy", so
     * both must produce the same shape — a record's `body` carries the
     * attachment lines the bubble deliberately splits out, and pasting those
     * raw paths would be a different feature wearing the same label. */
    function selectedTexts() {
      var texts = [];
      msgNodes().forEach(function (node) {
        if (ids.indexOf(idOf(node)) === -1) return;
        var bubble = node.querySelector(".bubble");
        texts.push(bubble ? bubble.textContent : "");
      });
      return texts;
    }

    function copySelected() {
      var payload = actions.joinTexts(selectedTexts());
      var count = ids.length;
      if (!payload) {
        host.showError("Nothing to copy — the selected messages have no text.");
        return;
      }
      if (!navigator.clipboard) {
        host.showError("Copy failed — the browser refused clipboard access.");
        return;
      }
      navigator.clipboard
        .writeText(payload)
        .then(function () {
          host.showNotice(count + " messages copied.");
          exit();
        })
        .catch(function () {
          host.showError("Copy failed — the browser refused clipboard access.");
        });
    }

    // ---- bulk forward ----------------------------------------------------

    /* Forward the selection to `toPeer`, ONE POST AT A TIME.
     *
     * Sequential rather than parallel, for two reasons that both bite. The
     * store appends in arrival order, so parallel POSTs would land the
     * conversation out of order at the far end; and they contend on the same
     * per-thread flock, so firing six at once turns a forward into a lock
     * convoy. Chaining costs a few hundred ms and is correct.
     *
     * A partial failure reports HOW FAR IT GOT rather than just "failed" —
     * the messages already delivered are not coming back, and the operator has
     * to know which half landed. */
    function forwardAll(toPeer, records) {
      var sent = 0;
      var chain = Promise.resolve();
      records.forEach(function (record) {
        chain = chain.then(function () {
          var to = actions.forwardOriginalTo(
            record,
            host.getPeer(),
            host.viewer,
          );
          return host
            .sendForwardBody(toPeer, actions.forwardBody(record, to))
            .then(function () {
              sent += 1;
            });
        });
      });
      chain
        .then(function () {
          host.onForwarded(toPeer, sent);
          exit();
        })
        .catch(function (err) {
          host.showError(
            "Forward failed after " +
              sent +
              " of " +
              records.length +
              ": " +
              err.message,
          );
        });
    }

    function forwardSelected() {
      var records = actions.selectedRecords(host.getMessages(), ids);
      if (!records.length) return;
      host.openForwardPicker($forward.getBoundingClientRect(), function (peer) {
        forwardAll(peer, records);
      });
    }

    // ---- the bar ---------------------------------------------------------

    /* Every bar button stops propagation.
     *
     * chat_menu dismisses its popovers on any document click landing outside
     * them, and this bar IS outside them — so without this, the tap that opens
     * the forward picker is also the tap that closes it. Found by reasoning
     * about the dismiss handler, not by watching it fail. */
    function wire(button, handler) {
      if (!button) return;
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        handler();
      });
    }

    wire($copy, copySelected);
    wire($forward, forwardSelected);
    wire($cancel, exit);

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") exit();
    });

    /* The marquee is mounted HERE rather than from chat_menu because its only
     * output is a list of ids and this module is what a list of ids means
     * something to. chat_menu is also within a few lines of its 512-line
     * budget, and a feature should not have to spend another file's budget. */
    if (root.ChatMarquee) root.ChatMarquee.mount({ messagesEl: $messages, onMarquee: addMany });

    paint();
    return {
      decorate: decorate,
      enter: enter,
      exit: exit,
      isActive: isActive,
      addMany: addMany,
      toggleAt: toggleAt,
    };
  }

  root.ChatSelect = { mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
