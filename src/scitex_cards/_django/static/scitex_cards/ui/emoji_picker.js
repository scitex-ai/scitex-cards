/* Emoji picker — a compact, self-contained composer affordance.
 *
 * WHY IT LIVES IN ui/ AND NOT chat/: this is a generic "insert a character
 * at the caret of a text field" widget with no knowledge of DMs, messages
 * or `.msg`. It is written to be HARVESTED into scitex-ui as a shared
 * primitive — the class names are self-describing (`stx-emoji-picker__*`),
 * the palette rides component tokens, and the only contract with the host
 * page is "point me at a text field".
 *
 * SELF-CONTAINED, deliberately: literal unicode emoji characters, no icon
 * font, no CDN, no network fetch, no bundled emoji database. This board is
 * served over a tunnel to a phone; anything external simply does not load,
 * and an icon glyph that fails to resolve renders an empty box exactly where
 * the affordance was. Same reasoning as the composer's literal paperclip.
 *
 * Two ways to mount:
 *   - declarative (what chat.html uses): put the attribute on any element
 *       <span data-stx-emoji-picker-for="compose-body"></span>
 *     and this file wires it on load. No host-page JS at all.
 *   - programmatic: StxEmojiPicker.mount({ field: node, mount: node })
 *
 * Consumed two ways, hence the UMD-lite head:
 *   - browser: <script src=emoji_picker.js> -> window.StxEmojiPicker
 *   - node (tests): require() -> module.exports, so the suite exercises THIS
 *     file rather than a hand-ported copy of its logic.
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
    root.StxEmojiPicker = api;
    api.autoMountWhenReady();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* A COMPACT set, on purpose. A searchable picker needs a name database;
   * a database is a payload, and a payload over a phone tunnel is the thing
   * we are avoiding. Three short rows cover what a chat with an agent fleet
   * actually uses: how you feel, what state the work is in, and what the
   * work is.
   *
   * Written as SPACE-SEPARATED ROWS rather than arrays for two reasons: the
   * whole set stays readable at a glance (an array-per-emoji is 60 lines of
   * quotes), and splitting on the space keeps multi-codepoint characters
   * intact. A per-character split would tear "❤️" (U+2764 U+FE0F) into a
   * heart plus a stray variation selector — and it is exactly those
   * selectors that stop ⚠️ / ⏱️ / ▶️ rendering as monochrome text glyphs. */
  var EMOJI_ROWS = [
    [
      "Reactions",
      "😀 😄 😅 😂 🙂 😉 😍 🤔 😴 😭 😱 😡 👍 👎 👏 🙏 💪 🎉 ❤️ 🔥",
    ],
    ["Status", "✅ ❌ ⚠️ ❗ ❓ ⏳ ⏱️ 🚧 🐛 🔧 🚀 📌 📎 📝 💡 🔍 ⭐ 🟢 🟡 🔴"],
    ["Work", "💻 📊 🧪 📦 🗂️ 🔗 📅 ☕ 👀 🤝 🙇 ✨ 🧠 🆗 ➕ ➖ 🔁 ⏸️ ▶️ 🛑"],
  ];

  var EMOJI_GROUPS = EMOJI_ROWS.map(function (row) {
    return { name: row[0], emoji: row[1].split(" ") };
  });

  var MOUNT_ATTR = "data-stx-emoji-picker-for";
  var READY_ATTR = "data-stx-emoji-picker-ready";
  var DEFAULT_LABEL = "🙂";

  // ---- pure helpers (node-testable, no DOM) --------------------------------

  /* Clamp a selection offset onto [0, len].
   *
   * A missing/NaN offset means "we do not know where the caret is" — some
   * fields report null for selectionStart — and the honest answer there is
   * the END of the text, not position 0. Inserting at 0 would silently
   * prepend the emoji to a message the operator already typed. */
  function clampOffset(offset, len) {
    var n = typeof offset === "number" ? offset : Number(offset);
    if (offset === null || offset === undefined || isNaN(n)) return len;
    if (n < 0) return 0;
    if (n > len) return len;
    return Math.floor(n);
  }

  /* Replace [start, end) of `value` with `insert`.
   *
   * Returns {value, caret} where `caret` is where the cursor belongs
   * afterwards: immediately AFTER the inserted text, so a second tap adds a
   * second emoji rather than overwriting the first. A reversed range is
   * normalised — a backwards drag-selection is a real thing a user can hand
   * us and must not corrupt the message.
   */
  function spliceText(value, start, end, insert) {
    var text = value === null || value === undefined ? "" : String(value);
    var piece = insert === null || insert === undefined ? "" : String(insert);
    var len = text.length;
    var from = clampOffset(start, len);
    var to = clampOffset(end, len);
    if (from > to) {
      var swap = from;
      from = to;
      to = swap;
    }
    return {
      value: text.slice(0, from) + piece + text.slice(to),
      caret: from + piece.length,
    };
  }

  /* Every emoji this picker can insert, in panel order. */
  function allEmoji() {
    var out = [];
    EMOJI_GROUPS.forEach(function (group) {
      group.emoji.forEach(function (character) {
        out.push(character);
      });
    });
    return out;
  }

  // ---- field insertion -----------------------------------------------------

  /* Tell listeners the field changed under them.
   *
   * The composer's own code (autosize, draft saving, send-button enabling)
   * listens for `input`, and a programmatic `.value =` fires nothing. Without
   * this the emoji is in the box but every listener still believes the box
   * holds the old text. */
  function notifyInput(field) {
    if (typeof field.dispatchEvent !== "function") return;
    if (typeof Event !== "function") return;
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /* Insert `emoji` at the field's caret and leave the caret after it.
   *
   * `range` ({start, end}) overrides the field's live selection. On touch the
   * field has usually been blurred by the tap that reached the panel, and a
   * blurred field is not a reliable place to ask where the caret was — see
   * `trackCaret`. Passing the remembered range is how a phone tap lands the
   * emoji where the operator was typing rather than at position 0.
   *
   * Duck-typed on purpose: anything with `value` (and optionally
   * selectionStart/selectionEnd/setSelectionRange/focus) works, which is
   * what lets node exercise this without a DOM. */
  function insertEmoji(field, emoji, range) {
    if (!field) return null;
    var where = range || {
      start: field.selectionStart,
      end: field.selectionEnd,
    };
    var result = spliceText(field.value, where.start, where.end, emoji);
    field.value = result.value;
    if (typeof field.setSelectionRange === "function") {
      field.setSelectionRange(result.caret, result.caret);
    }
    if (typeof field.focus === "function") field.focus();
    notifyInput(field);
    return result;
  }

  // ---- DOM ----------------------------------------------------------------

  function makeElement(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildPanel(doc, onPick) {
    var panel = makeElement(doc, "div", "stx-emoji-picker__panel");
    panel.hidden = true;
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", "Emoji");
    EMOJI_GROUPS.forEach(function (group) {
      panel.appendChild(
        makeElement(doc, "div", "stx-emoji-picker__group-label", group.name),
      );
      var grid = makeElement(doc, "div", "stx-emoji-picker__grid");
      group.emoji.forEach(function (character) {
        var item = makeElement(
          doc,
          "button",
          "stx-emoji-picker__item",
          character,
        );
        item.type = "button";
        item.setAttribute("aria-label", group.name + " " + character);
        item.addEventListener("click", function () {
          onPick(character);
        });
        grid.appendChild(item);
      });
      panel.appendChild(grid);
    });
    return panel;
  }

  /* Keep the text field focused when the panel is pressed — WITH A MOUSE.
   *
   * Cancelling the default of `mousedown` stops the button taking focus off
   * the textarea while still letting the click through, so the caret never
   * moves. The same trick on `touchstart` is a TRAP: cancelling it suppresses
   * the emulated click on iOS, so a tap would insert nothing on the one
   * device this feature exists for. Touch therefore keeps its default
   * behaviour and the caret comes from `trackCaret` instead.
   */
  function holdFocus(panel) {
    panel.addEventListener("mousedown", function (event) {
      event.preventDefault();
    });
  }

  /* Remember where the caret was WHILE the field still had focus.
   *
   * A tap on the picker blurs the textarea. Browsers disagree about what a
   * blurred field reports for selectionStart, and "0" is a plausible answer —
   * which would silently prepend every emoji to a half-written message. So
   * the caret is recorded on the events that fire while the field is focused,
   * and the returned reader prefers the LIVE selection only while the field
   * really is the focused element.
   *
   * Returns a function giving the range to insert at. */
  function trackCaret(field) {
    var remembered = null;
    function remember() {
      if (typeof field.selectionStart !== "number") return;
      remembered = { start: field.selectionStart, end: field.selectionEnd };
    }
    ["keyup", "click", "select", "input"].forEach(function (name) {
      field.addEventListener(name, remember);
    });
    return function () {
      var doc = field.ownerDocument;
      if (doc && doc.activeElement === field) {
        return { start: field.selectionStart, end: field.selectionEnd };
      }
      // No remembered caret means the operator never put one there; the end
      // of the text is the only honest place to append.
      return (
        remembered || { start: field.value.length, end: field.value.length }
      );
    };
  }

  /* Wire one picker onto one text field.
   *
   * `mount` is the host element; it becomes the picker root (the toggle
   * button and the popover are appended to it), so the host page decides
   * WHERE the affordance sits and this module decides what it looks like. */
  function mount(options) {
    var opts = options || {};
    var field = opts.field;
    var host = opts.mount;
    if (!field || !host) {
      throw new Error(
        "StxEmojiPicker.mount: both `field` and `mount` are required",
      );
    }
    var doc = host.ownerDocument;
    host.classList.add("stx-emoji-picker");

    var button = makeElement(
      doc,
      "button",
      "stx-emoji-picker__toggle",
      opts.label || DEFAULT_LABEL,
    );
    button.type = "button";
    button.title = opts.title || "Insert emoji";
    button.setAttribute("aria-haspopup", "true");
    button.setAttribute("aria-expanded", "false");

    var caretOf = trackCaret(field);
    var panel = buildPanel(doc, function (character) {
      insertEmoji(field, character, caretOf());
      // Panel deliberately STAYS OPEN: reactions arrive in twos and threes,
      // and reopening between each one is the friction this replaces.
    });
    holdFocus(panel);

    host.appendChild(button);
    host.appendChild(panel);

    function isOpen() {
      return !panel.hidden;
    }

    function close() {
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
      host.classList.remove("stx-emoji-picker--open");
    }

    function open() {
      // A disabled composer has nothing to insert INTO. Refusing here keeps
      // the picker from opening over a field that cannot accept text.
      if (field.disabled) return;
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      host.classList.add("stx-emoji-picker--open");
    }

    function toggle() {
      if (isOpen()) close();
      else open();
    }

    button.addEventListener("click", function (event) {
      event.preventDefault();
      toggle();
    });

    /* Outside press closes. `pointerdown` covers mouse, pen AND touch in one
     * listener, which is the point: a second touch-specific listener would
     * fire alongside it for every tap, and the touch event family is exactly
     * the one we must not be cancelling anything in. */
    function onOutside(event) {
      if (!isOpen()) return;
      if (host.contains(event.target)) return;
      close();
    }
    doc.addEventListener("pointerdown", onOutside);

    function onKeydown(event) {
      if (event.key === "Escape" && isOpen()) {
        close();
        if (typeof field.focus === "function") field.focus();
      }
    }
    doc.addEventListener("keydown", onKeydown);

    /* Mirror the field's own disabled state onto the toggle.
     *
     * The chat composer disables its textarea until a thread is open. A
     * toggle that still looks pressable there is a lie — you tap it and
     * nothing happens, which reads as a broken button rather than as
     * "pick an agent first". */
    function syncDisabled() {
      button.disabled = !!field.disabled;
      if (field.disabled) close();
    }
    syncDisabled();
    var observer = null;
    if (typeof MutationObserver === "function") {
      observer = new MutationObserver(syncDisabled);
      observer.observe(field, {
        attributes: true,
        attributeFilter: ["disabled"],
      });
    }

    function destroy() {
      doc.removeEventListener("pointerdown", onOutside);
      doc.removeEventListener("keydown", onKeydown);
      if (observer) observer.disconnect();
      host.removeChild(button);
      host.removeChild(panel);
      host.classList.remove("stx-emoji-picker");
      // Clear the wired marker too, or the host is left looking mounted and
      // a later autoMount would skip the element it just released.
      host.removeAttribute(READY_ATTR);
    }

    host.setAttribute(READY_ATTR, "1");
    return {
      element: host,
      button: button,
      panel: panel,
      field: field,
      open: open,
      close: close,
      toggle: toggle,
      isOpen: isOpen,
      destroy: destroy,
    };
  }

  /* Wire every declarative mount point in `doc`.
   *
   * Idempotent: an element already wired carries READY_ATTR and is skipped,
   * so calling this twice (script re-run, partial page swap) cannot produce
   * two toggles on one host. A mount point pointing at a missing field is
   * skipped rather than throwing — one broken marker must not take the whole
   * page's scripting down with it. */
  function autoMount(doc) {
    var scope = doc || (typeof document !== "undefined" ? document : null);
    if (!scope) return [];
    var mounted = [];
    // Ids are looked up on the DOCUMENT even when `scope` is a subtree —
    // getElementById is a document method, and the field a picker targets is
    // not required to live inside the same subtree as its mount point.
    var byId = scope.getElementById ? scope : scope.ownerDocument;
    var nodes = scope.querySelectorAll("[" + MOUNT_ATTR + "]");
    Array.prototype.forEach.call(nodes, function (host) {
      if (host.getAttribute(READY_ATTR)) return;
      var field = byId.getElementById(host.getAttribute(MOUNT_ATTR));
      if (!field) return;
      mounted.push(mount({ field: field, mount: host }));
    });
    return mounted;
  }

  function autoMountWhenReady() {
    if (typeof document === "undefined") return;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        autoMount(document);
      });
    } else {
      autoMount(document);
    }
  }

  return {
    EMOJI_GROUPS: EMOJI_GROUPS,
    MOUNT_ATTR: MOUNT_ATTR,
    allEmoji: allEmoji,
    autoMount: autoMount,
    autoMountWhenReady: autoMountWhenReady,
    clampOffset: clampOffset,
    insertEmoji: insertEmoji,
    mount: mount,
    spliceText: spliceText,
  };
});

/* EOF */
