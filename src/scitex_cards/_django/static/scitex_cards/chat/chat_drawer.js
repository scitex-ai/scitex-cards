/* Mobile agent-list drawer — open/close state, and the two obligations a
 * transform-hidden panel does NOT satisfy on its own.
 *
 * Extracted from chat.js (which was over its 512-line budget) while fixing two
 * live defects that scitex-ui found on 2026-07-28 harvesting this into a shared
 * component. Both were operator-facing on the phone surface they are migrating
 * onto, and both were invisible to a screenshot, which is why they survived.
 *
 * DEFECT 1 — A CLOSED DRAWER WAS STILL TABBABLE.
 * The panel is hidden with `transform: translateX(-105%)`. A transform moves
 * PIXELS. It does not remove an element from the tab order or the accessibility
 * tree. At phone width with the drawer shut, Tab from the header put focus into
 * the invisible agent list with no visible focus ring anywhere, and the next
 * Enter activated an agent link the operator could not see — from their side the
 * page simply jumped to another thread for no reason.
 *   `inert` stops the keyboard and assistive tech. `visibility` stops the
 *   pointer. NEITHER IMPLIES THE OTHER, so both are set, and both are asserted
 *   separately in the tests for exactly that reason.
 *
 * DEFECT 2 — THE DRAWER AND ITS SCRIM COULD DESYNC.
 * They were two bare `classList.toggle("open")` calls on two elements.
 * `toggle()` flips whatever is currently there, so the moment any path cleared
 * one without the other they diverged — and `close()` is genuinely called from
 * elsewhere (the thread-open handler). Once diverged, ONE tap put them in
 * OPPOSITE states. The bad half is a scrim with no drawer: the screen greys out,
 * nothing dismisses it, and the menu button sits BEHIND the scrim, so the only
 * way out is a force-reload. If the operator ever reported "the menu got stuck",
 * this is the candidate.
 *   ONE BOOLEAN OWNS THE STATE and both elements are rendered from it. Do not
 *   reintroduce a bare toggle on either element.
 *
 * This is a stopgap in the same sense chat_menu.js is: scitex-ui has the drawer
 * as a shared component (their PR #106) with focus trapping, focus restore and
 * body-scroll lock, which this does not do. When it ships in a release we can
 * install, delete this and mount theirs — the element ids do not change.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  /* Mount the drawer.
   *
   * `panel`, `scrim` and `trigger` are elements; the caller owns the lookup so
   * this module never guesses at ids. Returns `{ close, isOpen }` — `close` is
   * what other handlers call when opening a thread should dismiss the drawer.
   */
  function mount(options) {
    var panel = options.panel;
    var scrim = options.scrim;
    var trigger = options.trigger;
    if (!panel || !scrim || !trigger) return null;

    var open = false;

    function render() {
      panel.classList.toggle("open", open);
      scrim.classList.toggle("open", open);
      panel.inert = !open;
      panel.style.visibility = open ? "" : "hidden";
      trigger.setAttribute("aria-expanded", String(open));
    }

    function close() {
      open = false;
      render();
    }

    trigger.addEventListener("click", function () {
      open = !open;
      render();
    });
    scrim.addEventListener("click", close);

    /* Keyboard-openable but pointer-only-closable is a trap. */
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && open) close();
    });

    /* Establish the CLOSED state at load. Without this the panel is neither
     * inert nor visibility-hidden until the first interaction — which is
     * precisely the window in which defect 1 bit, because the operator lands on
     * the page and presses Tab before ever touching the menu button. */
    render();

    return {
      close: close,
      isOpen: function () {
        return open;
      },
    };
  }

  root.ChatDrawer = { mount: mount };
})(window);

/* EOF */
