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

  /* Is this click target a VIEW control that selection mode must let through?
   *
   * Module scope, not a closure inside mount(), so the rule is a named thing
   * with a test rather than a condition buried in a listener — this exact
   * decision has now been got wrong once and it is not self-evident.
   *
   * THE RULE IS WHAT THE CONTROL DOES, not that it is a control. Following an
   * attachment link navigates away; toggling a reaction mutates the thread.
   * Both are genuinely a second answer to one gesture and both stay BLOCKED.
   * Expanding a clamped bubble, or copying it to a file, only changes what you
   * can SEE — which is if anything a prerequisite for deciding whether to
   * select it. Those live in `.longtext-tools`, so that container is the rule.
   */
  function isViewControl(target) {
    return !!(target && target.closest && target.closest(".longtext-tools"));
  }

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

    function enter(node) {
      active = true;
      // Seed with the message the menu was opened on: the operator asked to
      // select THAT one, so an empty selection would throw away the tap that
      // got them here.
      var seed = idOf(node);
      ids = seed ? [seed] : [];
      $messages.classList.add("selecting");
      $bar.classList.add("open");
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
    /* ...but a VIEW control is not one of those handlers, and swallowing it
     * makes long messages unreadable for as long as you are selecting. Operator,
     * 2026-07-29: 「ショーオールをクリックすると、それに対応するメッセージが選択
     * されてしまって、エクスパンドすることができません」 — "Show all" selected the
     * message instead of expanding it. See isViewControl above for the rule. */
    $messages.addEventListener(
      "click",
      function (event) {
        if (!active) return;
        if (isViewControl(event.target)) return; // let the control do its job
        var node = event.target.closest ? event.target.closest(".msg") : null;
        if (!node || !idOf(node)) return;
        event.preventDefault();
        event.stopPropagation();
        ids = actions.toggleSelection(ids, idOf(node));
        paint();
      },
      true,
    );

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
          return host
            .sendForwardBody(toPeer, actions.forwardBody(record))
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

    paint();
    return {
      decorate: decorate,
      enter: enter,
      exit: exit,
      isActive: isActive,
    };
  }

  root.ChatSelect = { isViewControl: isViewControl, mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
