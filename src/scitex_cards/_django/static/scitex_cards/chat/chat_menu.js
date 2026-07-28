/* Message context menu for the DM chat pane — Reply, Copy, React, Forward.
 *
 * Split out of chat.js, which was already over the 512-line budget before
 * React and Forward existed. This file owns every DOM node and every fetch
 * that belongs to the menu; the pure decisions behind it (what body a forward
 * produces, what a reaction tap means, how chips order) live in chat_actions.js
 * so they can be exercised under node.
 *
 * chat.js hands in the seams it owns via mount() — the API base, the error
 * bar, the thread refresh, the current peer, the current reactions, the
 * composer field, the agent list. Nothing here reaches back into chat.js.
 *
 * Look and markup come from scitex-ui (.stx-app-context-menu, >=0.11.1). Only
 * the MECHANICS are here, and only until scitex-ui's ts/app/context-menu
 * module lands — they confirmed base ships the stylesheet and zero lines of
 * behaviour, which is exactly the private-implementation gap this is meant to
 * avoid. When their module ships, delete the open/clamp/dismiss half; the
 * markup it emits is identical, so nothing here leaks into the template.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  var actions = root.ChatActions;

  /* Viewport inset for a clamped popover — a menu opened near the right or
   * bottom edge would otherwise render half off-screen, which on a phone
   * means unreachable. */
  var EDGE_PAD_PX = 8;

  /* Longest quote a Reply prefills before it is elided. */
  var QUOTE_MAX = 140;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function mount(host) {
    /* The wrap is the whole primary popover: the quick reaction ROW on top,
     * the action LIST beneath it, positioned and dismissed as ONE thing. That
     * is the operator's sketch, and it is also why the row cannot drift away
     * from the menu — there is only one positioned element. */
    var $wrap = document.getElementById("msg-menu-wrap");
    var $quick = document.getElementById("react-quick");
    var $react = document.getElementById("react-bar");
    var $forward = document.getElementById("forward-menu");
    var $messages = host.messagesEl;
    var $body = host.composerEl;
    if (!$wrap || !$messages) return null;

    var target = null; // the .msg node the menu was opened on
    var select = null; // the selection-mode module, mounted at the bottom

    /* Every popover this module owns. Closing "the menu" must close all
     * three: leaving the emoji bar hanging after the menu that spawned it is
     * gone reads as a stuck UI, and it is the kind of thing only ever noticed
     * on the phone. */
    function panels() {
      return [$wrap, $react, $forward].filter(Boolean);
    }

    function closeAll() {
      panels().forEach(function (panel) {
        panel.classList.remove("open");
      });
      target = null;
      // A pending peer-picker handler belongs to the popover that is closing.
      // Left set, the NEXT forward would run the previous one's callback.
      forwardPick = null;
    }

    function closeSubPanels() {
      [$react, $forward].filter(Boolean).forEach(function (panel) {
        panel.classList.remove("open");
      });
    }

    /* Open `panel` at (x, y), clamped inside the viewport. */
    function openAt(panel, x, y) {
      panel.classList.add("open");
      var rect = panel.getBoundingClientRect();
      var maxX = window.innerWidth - rect.width - EDGE_PAD_PX;
      var maxY = window.innerHeight - rect.height - EDGE_PAD_PX;
      panel.style.left = Math.max(EDGE_PAD_PX, Math.min(x, maxX)) + "px";
      panel.style.top = Math.max(EDGE_PAD_PX, Math.min(y, maxY)) + "px";
    }

    function messageTextOf(node) {
      var bubble = node ? node.querySelector(".bubble") : null;
      return bubble ? bubble.textContent : "";
    }

    /* The stored record behind a rendered .msg node.
     *
     * chat.js stamps the message id onto the node when it paints it, which is
     * what lets an ACTION address the record rather than the pixels. Reacting
     * to "the text in this bubble" would attach the reaction to whatever
     * happened to be on screen; reacting to an id survives a repaint. */
    function recordOf(node) {
      if (!node) return null;
      var id = node.getAttribute("data-msg-id");
      if (!id) return null;
      var found = null;
      (host.getMessages() || []).forEach(function (m) {
        if (String(m.id) === id) found = m;
      });
      return found;
    }

    // ---- Reply / Copy ----------------------------------------------------

    var $reply = document.getElementById("mm-reply");
    var $copy = document.getElementById("mm-copy");

    if ($reply) {
      $reply.addEventListener("click", function () {
        // Quote-prefill rather than a threaded reply: the store has no parent
        // pointer yet (that arrives with the DM move into cards.db), so a
        // visible quote is honest about what it is. scitex-ui is designing the
        // reply-quote BLOCK in base; this is the composer half.
        var text = messageTextOf(target).trim();
        if (text) {
          var oneLine = text.replace(/\s+/g, " ");
          var quoted =
            oneLine.length > QUOTE_MAX
              ? oneLine.slice(0, QUOTE_MAX) + "…"
              : oneLine;
          var sep = $body.value && !/\n$/.test($body.value) ? "\n" : "";
          $body.value += sep + "> " + quoted + "\n\n";
        }
        closeAll();
        $body.focus();
      });
    }

    if ($copy) {
      $copy.addEventListener("click", function () {
        var text = messageTextOf(target);
        if (text && navigator.clipboard) {
          navigator.clipboard.writeText(text).catch(function () {
            host.showError(
              "Copy failed — the browser refused clipboard access.",
            );
          });
        }
        closeAll();
      });
    }

    // ---- React -----------------------------------------------------------

    /* POST one reaction event and repaint from the server's answer.
     *
     * The endpoint replies with the message's REFOLDED reactions rather than
     * an echo of the event, so the tapping client renders exactly what every
     * other client will see on its next poll instead of predicting the result
     * of its own write. */
    function sendReaction(messageId, emoji, action) {
      var peer = host.getPeer();
      if (!peer || !messageId) return;
      fetch(
        host.apiBase + "/dm/thread/" + encodeURIComponent(peer) + "/reaction",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message_id: messageId,
            emoji: emoji,
            action: action,
          }),
        },
      )
        .then(function (resp) {
          return resp.json().then(function (data) {
            if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
            return data;
          });
        })
        .then(function () {
          host.refreshThread();
        })
        .catch(function (err) {
          host.showError("Reaction failed: " + err.message);
        });
    }

    /* One tappable emoji. The SAME button for the quick row and for the
     * chevron's fuller picker — they are two views of one palette, so they
     * must also be one behaviour. */
    function emojiButton(emoji) {
      var button = el("button", "react-emoji", emoji);
      button.type = "button";
      button.setAttribute("aria-label", "React " + emoji);
      button.addEventListener("click", function () {
        var record = recordOf(target);
        if (!record) {
          closeAll();
          return;
        }
        var current = (host.getReactions()[record.id] || {})[emoji] || [];
        sendReaction(
          record.id,
          emoji,
          actions.nextAction(current, host.viewer),
        );
        closeAll();
      });
      return button;
    }

    /* Build both emoji panels once; neither set changes at runtime.
     *
     * The quick row's buttons are INSERTED BEFORE the chevron, which the
     * template already ships — so the row reads ⭕ ❌ ❓ … ▾ with the escape
     * hatch at the end, exactly as the reference has it. */
    function buildEmojiPanels() {
      var $more = document.getElementById("mm-react");
      if ($quick && $more) {
        actions.QUICK_REACTION_EMOJI.forEach(function (emoji) {
          $quick.insertBefore(emojiButton(emoji), $more);
        });
      }
      if (!$react) return;
      $react.textContent = "";
      actions.REACTION_EMOJI.forEach(function (emoji) {
        $react.appendChild(emojiButton(emoji));
      });
    }

    /* The chevron. It is the old "React…" ITEM, moved into the row: same id,
     * same job — swap the primary popover for the full palette, so only one
     * thing is ever on screen. */
    var $mmReact = document.getElementById("mm-react");
    if ($mmReact && $react) {
      $mmReact.addEventListener("click", function () {
        var rect = $wrap.getBoundingClientRect();
        $wrap.classList.remove("open");
        openAt($react, rect.left, rect.top);
      });
    }

    // ---- Forward ---------------------------------------------------------

    /* Who the picked peer is handed to. Set by whoever opened the picker —
     * the single Forward item, or selection mode's bulk Forward — so there is
     * ONE peer picker on the page rather than two that could disagree about
     * who exists. */
    var forwardPick = null;

    /* Populate the peer picker from the agent list chat.js already polls —
     * a second /dm/threads round-trip would only add a way for the picker to
     * disagree with the drawer about who exists. */
    function buildForwardMenu() {
      if (!$forward) return;
      $forward.textContent = "";
      var peers = (host.getAgents() || []).filter(function (a) {
        return a.name && a.name !== host.getPeer();
      });
      if (!peers.length) {
        var empty = el("div", "forward-empty", "No other agent to forward to.");
        $forward.appendChild(empty);
        return;
      }
      peers.forEach(function (agent) {
        var item = el("button", "stx-app-context-menu__item", agent.name);
        item.type = "button";
        item.setAttribute("role", "menuitem");
        item.addEventListener("click", function () {
          // Captured BEFORE closeAll, which clears the pending handler.
          var pick = forwardPick;
          closeAll();
          if (pick) pick(agent.name);
        });
        $forward.appendChild(item);
      });
    }

    /* Open the peer picker anchored at `rect`, handing the choice to `onPick`. */
    function openForwardPicker(rect, onPick) {
      if (!$forward) return;
      $wrap.classList.remove("open");
      forwardPick = onPick;
      buildForwardMenu();
      openAt($forward, rect.left, rect.top);
    }

    /* Forward = an ordinary message to another peer whose body opens with a
     * "[forwarded from <name>, <ts>]" banner. No new endpoint, no new field,
     * no new record kind — the existing compose path carries it, and an older
     * client shows the banner as text instead of showing nothing. */
    /* POST one already-composed body to `toPeer`, resolving on success.
     *
     * The ONE forward POST on the page. Selection mode's bulk forward chains
     * this rather than owning a second copy — two POSTs to the same endpoint
     * would be two places for the error handling to drift apart. */
    function sendForwardBody(toPeer, body) {
      return fetch(host.apiBase + "/dm/thread/" + encodeURIComponent(toPeer), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: body }),
      }).then(function (resp) {
        return resp.json().then(function (data) {
          if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
          return data;
        });
      });
    }

    function sendForward(toPeer, record) {
      sendForwardBody(toPeer, actions.forwardBody(record))
        .then(function () {
          host.onForwarded(toPeer, 1);
        })
        .catch(function (err) {
          host.showError("Forward failed: " + err.message);
        });
    }

    var $mmForward = document.getElementById("mm-forward");
    if ($mmForward && $forward) {
      $mmForward.addEventListener("click", function () {
        // Captured BEFORE the picker opens: openForwardPicker closes the wrap,
        // and the eventual pick happens long after `target` has been cleared.
        var record = recordOf(target);
        openForwardPicker($wrap.getBoundingClientRect(), function (peer) {
          if (record) sendForward(peer, record);
        });
      });
    }

    // ---- open / dismiss --------------------------------------------------

    function openMenuFor(node, x, y) {
      target = node;
      closeSubPanels();
      openAt($wrap, x, y);
    }

    /* In selection mode a press means "add this to the selection", so opening
     * the menu on top of it would put two different answers under one gesture.
     * Selection mode has its own bar for its own actions. */
    function menuSuppressed() {
      return !!(select && select.isActive());
    }

    $messages.addEventListener("contextmenu", function (event) {
      var node = event.target.closest ? event.target.closest(".msg") : null;
      if (!node || menuSuppressed()) return; // blank space keeps the browser menu
      event.preventDefault();
      openMenuFor(node, event.clientX, event.clientY);
    });

    /* Touch has no right-click, and this board exists for a phone. A long
     * press is the platform-native way to ask "what can I do with this?" —
     * without it, React and Forward would be desktop-only features on a
     * mobile-first page. Movement cancels, so a press that turns into a scroll
     * does not fire a menu the operator did not ask for. */
    var LONG_PRESS_MS = 420;
    var MOVE_CANCEL_PX = 10;
    var pressTimer = null;
    var pressAt = null;

    function clearPress() {
      if (pressTimer) clearTimeout(pressTimer);
      pressTimer = null;
      pressAt = null;
    }

    $messages.addEventListener(
      "touchstart",
      function (event) {
        var node = event.target.closest ? event.target.closest(".msg") : null;
        if (!node || event.touches.length !== 1 || menuSuppressed()) return;
        var touch = event.touches[0];
        pressAt = { x: touch.clientX, y: touch.clientY };
        pressTimer = setTimeout(function () {
          openMenuFor(node, pressAt.x, pressAt.y);
          clearPress();
        }, LONG_PRESS_MS);
      },
      { passive: true },
    );

    $messages.addEventListener(
      "touchmove",
      function (event) {
        if (!pressAt || !event.touches.length) return;
        var touch = event.touches[0];
        if (
          Math.abs(touch.clientX - pressAt.x) > MOVE_CANCEL_PX ||
          Math.abs(touch.clientY - pressAt.y) > MOVE_CANCEL_PX
        ) {
          clearPress();
        }
      },
      { passive: true },
    );

    ["touchend", "touchcancel"].forEach(function (name) {
      $messages.addEventListener(name, clearPress, { passive: true });
    });

    document.addEventListener("click", function (event) {
      var inside = panels().some(function (panel) {
        return panel.contains(event.target);
      });
      if (!inside) closeAll();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeAll();
    });
    // Scroll dismissal: the panels are position:fixed, so they would otherwise
    // hang in place while the message they belong to scrolls away underneath.
    $messages.addEventListener("scroll", closeAll);

    // ---- reaction chips --------------------------------------------------

    /* Paint a message's reaction chips into its already-built node.
     *
     * chat.js calls this from messageNode and says only WHERE the chips go —
     * every reaction read and write stays in this module, so there is one
     * place that knows what a reaction is.
     *
     * A chip is also a one-tap toggle. Once a reaction exists, going back
     * through the context menu to add or drop yours is three taps for a
     * gesture that should be one.
     */
    function renderReactions(wrap, message) {
      // The per-message PAINT HOOK, so selection mode gets its highlight back
      // after a poll rebuilds the pane. Without this the selection would look
      // cleared every 5s while still being held — and the operator would
      // re-tap a message that was already in the set, silently removing it.
      if (select) select.decorate(wrap, message);
      var chips = actions.chipsOf(host.getReactions()[message.id], host.viewer);
      if (!chips.length) return null;
      var row = el("div", "reactions");
      chips.forEach(function (chip) {
        var button = el("button", "chip" + (chip.mine ? " mine" : ""));
        button.type = "button";
        button.title = actions.actorsLabel(chip.actors);
        button.appendChild(el("span", "chip-emoji", chip.emoji));
        button.appendChild(el("span", "chip-count", String(chip.count)));
        button.addEventListener("click", function () {
          sendReaction(
            message.id,
            chip.emoji,
            actions.nextAction(chip.actors, host.viewer),
          );
        });
        row.appendChild(button);
      });
      wrap.appendChild(row);
      return row;
    }

    // ---- selection mode --------------------------------------------------

    /* Mounted HERE rather than from chat.js: this module already holds every
     * seam selection needs — the message list, the current peer, the one
     * forward POST and the peer picker — so routing them through chat.js would
     * add a pass-through and nothing else. chat.js is also at its 512-line
     * budget, and a feature should not have to spend another file's budget to
     * exist. */
    if (root.ChatSelect) {
      select = root.ChatSelect.mount({
        messagesEl: $messages,
        showError: host.showError,
        showNotice: host.showNotice,
        getMessages: host.getMessages,
        openForwardPicker: openForwardPicker,
        sendForwardBody: sendForwardBody,
        onForwarded: host.onForwarded,
      });
    }

    var $mmSelect = document.getElementById("mm-select");
    if ($mmSelect && select) {
      $mmSelect.addEventListener("click", function () {
        var node = target;
        closeAll();
        select.enter(node);
      });
    } else if ($mmSelect) {
      // The module failed to mount, so the item cannot work. Removing it is
      // the honest outcome: a menu entry that does nothing is worse than one
      // that is not there.
      $mmSelect.remove();
    }

    buildEmojiPanels();
    return { close: closeAll, renderReactions: renderReactions };
  }

  root.ChatMenu = { mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
