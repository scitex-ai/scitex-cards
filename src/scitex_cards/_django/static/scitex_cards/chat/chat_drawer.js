/* Mobile agent-list drawer — open/close state, and the two obligations a
 * transform-hidden panel does NOT satisfy on its own.
 *
 * THE PANEL IS NOT ALWAYS A DRAWER. Read DEFECT 3 before touching render().
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
 * DEFECT 3 — THE DESKTOP SIDEBAR WAS BLANKED BY THE DRAWER'S OWN FIX.
 * Mine, 2026-07-29, reported by the operator twice. The panel is `#agents`, and
 * ABOVE the 720px breakpoint it is not a drawer at all — it is the permanently
 * visible agent sidebar. render() applied `inert` and an inline
 * `visibility: hidden` UNCONDITIONALLY, and "closed" is its state at mount, so
 * on desktop the agent list vanished the moment this module loaded. An inline
 * style beats the stylesheet, so no CSS could win it back. The DM page showed
 * "No agent selected." beside an EMPTY sidebar while /dm/threads was returning
 * 15 agents and the tab title was counting unread correctly — which is why
 * checking the API said everything was fine. It was the PAGE that was broken.
 *   The fix asks the DOM which mode it is in instead of hard-coding 720 here,
 *   where it would drift from the stylesheet the first time the breakpoint
 *   moved: the hamburger is `display: none` above the breakpoint, so its
 *   COMPUTED display already answers "am I a drawer right now".
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

    /* Is the panel behaving as a DRAWER right now, or as the always-visible
     * desktop sidebar? The hamburger is `display: none` above the drawer
     * breakpoint, so asking for its COMPUTED display gets the answer from the
     * stylesheet itself — no breakpoint number duplicated in JS to drift out of
     * step with the CSS the next time it moves. If the query throws, assume
     * drawer: that keeps the accessibility obligations of DEFECT 1. */
    function inDrawerMode() {
      try {
        return window.getComputedStyle(trigger).display !== "none";
      } catch (err) {
        return true;
      }
    }

    function render() {
      panel.classList.toggle("open", open);
      scrim.classList.toggle("open", open);
      trigger.setAttribute("aria-expanded", String(open));

      /* On desktop the panel is the permanent sidebar and neither property
       * belongs on it — see DEFECT 3. Clear BOTH and let the stylesheet decide.
       * Both, because they cover different senses: clearing only `visibility`
       * gives back a sidebar that looks right and cannot be reached by keyboard
       * or read by a screen reader, which is a worse bug than the visible one
       * because nobody can see it. */
      if (!inDrawerMode()) {
        panel.inert = false;
        panel.style.visibility = "";
        return;
      }

      /* `inert` stops the keyboard and assistive tech, `visibility` stops the
       * pointer, and neither implies the other — so a closed drawer needs both.
       */
      panel.inert = !open;
      panel.style.visibility = open ? "" : "hidden";
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

    /* Establish the CLOSED state at load. On a phone, without this the panel is
     * neither inert nor visibility-hidden until the first interaction — which is
     * precisely the window in which defect 1 bit, because the operator lands on
     * the page and presses Tab before ever touching the menu button. This same
     * call is where defect 3 bit on desktop, which is why render() now asks what
     * mode it is in rather than assuming. */
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
