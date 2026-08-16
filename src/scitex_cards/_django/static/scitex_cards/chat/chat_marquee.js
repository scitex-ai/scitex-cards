/* Drag-rectangle ("marquee") selection over the DM thread.
 *
 * THE OPERATOR'S SPEC, and the whole design in one sentence: THE START
 * POSITION DECIDES. They wrote it after testing the live page —
 * 「矩形選択をしようとするとテキストが反転されてしまうんですね」 — and were
 * explicit that this is not "turn text selection off":
 *
 *   1. drag STARTS ON BLANK SPACE  -> rectangle-selects MESSAGES, Telegram
 *      style, and the text must NOT invert.
 *   2. drag STARTS ON MESSAGE TEXT -> ordinary NATIVE text selection, so the
 *      text can be copied, and it DOES invert.
 *
 * Both behaviours coexist. The choice is made once, at pointerdown, from what
 * is under the pointer — not from a modifier key and not from a mode toggle —
 * and is then committed to for the whole drag. Suppressing selection globally
 * would satisfy (1) and destroy (2), which is why this module never touches
 * user-select outside an armed drag.
 *
 * TOUCH IS DELIBERATELY EXCLUDED. On a phone a drag is a SCROLL, and the
 * operator's stated first priority is using this page away from their desk;
 * a marquee that ate the scroll gesture would cost them more than it gave.
 * Touch already has its own path to a selection — long-press opens the menu,
 * whose Select item seeds it — so nothing is unreachable. This is the card's
 * instruction ("say explicitly what you do on touch"), answered out loud.
 *
 * Mounted by chat_select.js, which owns the selection this feeds.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  var actions = root.ChatActions;

  /* Below this the gesture was a CLICK, not a drag.
   *
   * Without a threshold every stray click on blank space would commit a
   * zero-area marquee, and a zero-area marquee selects nothing — so a
   * mis-click would silently wipe a selection the operator had just built.
   */
  var DRAG_MIN_PX = 4;

  /* Where a pointerdown means "let the browser select text" rather than
   * "start a rectangle": the message body itself, and anything the operator
   * can actually operate. `.bubble` is the text; the rest are the controls
   * that live among the messages (reaction chips, attachment links, the
   * long-text expander), none of which should be swallowed by a drag. */
  var TEXT_SELECTOR = ".bubble, a, button, input, textarea, select, .reactions";

  /* Does a pointerdown HERE mean "select text" rather than "draw a rectangle"?
   *
   * Module scope, not a closure inside mount(), so THE decision this whole
   * module turns on is a named thing with a test rather than a condition
   * buried in a listener — the same reason chat_select hoisted isViewControl
   * after getting the equivalent rule wrong once.
   *
   * This IS the operator's spec: the start position decides, and it decides
   * once. True means the browser keeps the gesture and the text inverts as it
   * always has; false means blank space, so the rectangle takes it.
   */
  function startsTextSelection(target) {
    return !!(target && target.closest && target.closest(TEXT_SELECTOR));
  }

  function mount(host) {
    var $messages = host.messagesEl;
    if (!$messages || !actions) return null;

    var origin = null; // {x, y} in CONTENT coords (viewport + scrollTop)
    var armed = false; // has the drag passed DRAG_MIN_PX yet?
    var $box = null;
    var pointerId = null;

    /* Content coords, not viewport coords.
     *
     * The thread scrolls under the drag, so an anchor stored in viewport
     * space would slide whenever the wheel moved — the rectangle would
     * detach from the message it started on. Adding scrollTop pins the
     * anchor to the CONTENT, so a mid-drag scroll extends the selection
     * instead of corrupting it.
     */
    function pointAt(event) {
      return {
        x: event.clientX,
        y: event.clientY + $messages.scrollTop,
      };
    }

    function boxes() {
      return Array.prototype.slice
        .call($messages.querySelectorAll(".msg[data-msg-id]"))
        .map(function (node) {
          var r = node.getBoundingClientRect();
          var top = r.top + $messages.scrollTop;
          return {
            id: node.getAttribute("data-msg-id"),
            rect: { left: r.left, right: r.right, top: top, bottom: top + r.height },
          };
        });
    }

    function ensureBox() {
      if ($box) return $box;
      $box = document.createElement("div");
      $box.id = "marquee-box";
      $box.setAttribute("aria-hidden", "true");
      document.body.appendChild($box);
      return $box;
    }

    /* Paint the rectangle back in VIEWPORT space — it is position:fixed, so
     * it has to undo the scrollTop that `pointAt` added. */
    function paint(rect) {
      var node = ensureBox();
      node.style.left = rect.left + "px";
      node.style.top = rect.top - $messages.scrollTop + "px";
      node.style.width = Math.max(0, rect.right - rect.left) + "px";
      node.style.height = Math.max(0, rect.bottom - rect.top) + "px";
      node.classList.add("open");
    }

    function teardown() {
      if ($box) $box.classList.remove("open");
      $messages.classList.remove("marquee-dragging");
      if (pointerId !== null && $messages.releasePointerCapture) {
        try {
          $messages.releasePointerCapture(pointerId);
        } catch (err) {
          /* capture already gone — nothing to release */
        }
      }
      origin = null;
      armed = false;
      pointerId = null;
    }

    function onDown(event) {
      // Touch scrolls; pen and mouse draw. See the header.
      if (event.pointerType === "touch") return;
      // Primary button only: right-click belongs to the context menu, and
      // middle-click to the browser.
      if (event.button !== 0) return;
      var target = event.target;
      if (startsTextSelection(target)) return; // case 2: the browser keeps it
      if (!target.closest || !target.closest("#messages")) return;

      origin = pointAt(event);
      armed = false;
      pointerId = event.pointerId;
      if ($messages.setPointerCapture) {
        try {
          $messages.setPointerCapture(event.pointerId);
        } catch (err) {
          /* capture unsupported here — the drag still works via document */
        }
      }
    }

    function onMove(event) {
      if (!origin) return;
      var now = pointAt(event);
      if (!armed) {
        if (
          Math.abs(now.x - origin.x) < DRAG_MIN_PX &&
          Math.abs(now.y - origin.y) < DRAG_MIN_PX
        )
          return;
        armed = true;
        // Only NOW is the native selection suppressed — case 2 must keep it.
        $messages.classList.add("marquee-dragging");
      }
      // Armed: this gesture is a rectangle, so the browser must stop trying to
      // drag a caret through it.
      event.preventDefault();
      paint(actions.marqueeRect(origin, now));
    }

    function onUp(event) {
      if (!origin) return;
      var wasArmed = armed;
      var rect = actions.marqueeRect(origin, pointAt(event));
      teardown();
      if (!wasArmed) return; // a click, not a drag
      host.onMarquee(actions.idsWithin(boxes(), rect));
    }

    $messages.addEventListener("pointerdown", onDown);
    $messages.addEventListener("pointermove", onMove);
    $messages.addEventListener("pointerup", onUp);
    $messages.addEventListener("pointercancel", teardown);

    /* Escape abandons the drag without selecting.
     *
     * The rectangle is a committing gesture with no other way out once the
     * button is down — and the same key already means "never mind" for the
     * menu and for selection mode, so it should not mean something else here.
     */
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && origin) teardown();
    });

    return { isDragging: function () { return armed; } };
  }

  root.ChatMarquee = {
    mount: mount,
    startsTextSelection: startsTextSelection,
  };
})(typeof self !== "undefined" ? self : this);

/* EOF */
