/* Long-press gesture over the DM thread — touch's stand-in for right-click.
 *
 * Touch has no right-click, and this board exists for a phone. A long press is
 * the platform-native way to ask "what can I do with this?"; without it, React
 * and Forward would be desktop-only features on a mobile-first page. Movement
 * cancels, so a press that turns into a scroll does not fire a menu the
 * operator did not ask for.
 *
 * Lifted out of chat_menu.js when that file reached its 512-line budget. It is
 * a cohesive thing on its own — a GESTURE recogniser that knows nothing about
 * menus — so the split is along a real seam rather than at a convenient line
 * number: the host says what a recognised press should DO.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  /* Long enough not to fire on a tap, short enough not to feel broken. */
  var LONG_PRESS_MS = 420;

  /* Past this the finger is scrolling, not pressing. */
  var MOVE_CANCEL_PX = 10;

  function mount(host) {
    var $messages = host.messagesEl;
    if (!$messages) return null;

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
        if (!node || event.touches.length !== 1 || host.suppressed()) return;
        var touch = event.touches[0];
        pressAt = { x: touch.clientX, y: touch.clientY };
        pressTimer = setTimeout(function () {
          host.onLongPress(node, pressAt.x, pressAt.y);
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

    return { cancel: clearPress };
  }

  root.ChatLongPress = { mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
