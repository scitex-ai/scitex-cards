/* app_router.js — Board | DM single-page router for the unified Cards page.
 *
 * ONE page, TWO panes: `#dm-pane` (default, preserves the CCT4610 landing)
 * and `#board-pane` (the extracted board, same partial the standalone board
 * serves). The hash names the pane — `#dm` / `#board` — so the header never
 * reloads and both panes preload their data in parallel on first paint
 * (board graph fetch + DM agent/thread polls all start on page load, whether
 * or not their pane is visible).
 *
 * Same structure as timelineControls.js: pure toggle helper (node-tested
 * below via module.exports) + DOM wiring that short-circuits under node.
 * No build step — served static, loaded `defer` after both panes' scripts.
 */
"use strict";

(function () {
  var PANES = ["dm", "board"];

  function paneForHash(hash) {
    var h = String(hash == null ? "" : hash).replace(/^#/, "");
    return PANES.indexOf(h) === -1 ? "dm" : h;
  }

  function applyPane(root, which) {
    var out = {};
    PANES.forEach(function (name) {
      var el =
        root.querySelector && root.querySelector("#" + name + "-pane");
      var on = name === which;
      if (el) {
        if (on) el.removeAttribute("hidden");
        else el.setAttribute("hidden", "");
      }
      out[name] = on;
    });
    var items =
      root.querySelectorAll &&
      root.querySelectorAll(".stx-cards-switcher__item");
    if (items) {
      Array.prototype.forEach.call(items, function (a) {
        var href = a.getAttribute("href") || "";
        var isBoard = href.slice(-6) === "/board" || href.slice(-6) === "#board";
        var want = which === "board" ? isBoard : !isBoard;
        if (want) a.classList.add("stx-cards-switcher__item--on");
        else a.classList.remove("stx-cards-switcher__item--on");
        if (want) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
      });
    }
    return out;
  }

  function show(root, which) {
    var state = applyPane(root, which);
    repaintTitle(which);
    // A pane shown after being hidden may have measured zero-size containers
    // (timeline raster). Nudge listeners; board + DM renderers that poll
    // repaint on their own tick regardless.
    if (typeof window !== "undefined" && window.dispatchEvent) {
      window.dispatchEvent(new Event("resize"));
    }
    return state;
  }

  /* The chrome title belongs to the VISIBLE pane. Each pane repaints its
   * own title on its own poll tick, but a tick can be up to ~10s away — so
   * the router repaints on every switch instead of leaving the other pane's
   * title stale (2026-10-31: #board showing "Cards DM (3)"). Panes opt in by
   * registering window.STX.paneTitle[which]; absent hooks (standalone pages,
   * tests) are a no-op, never a throw — a title must not break a switch. */
  function repaintTitle(which) {
    try {
      var NS = typeof window !== "undefined" ? window.STX : null;
      var hooks = NS && NS.paneTitle;
      var fn = hooks && hooks[which];
      if (typeof fn === "function") fn();
    } catch (e) {
      /* title is decoration; ignore */
    }
  }

  function mount(doc) {
    if (!doc || typeof doc.addEventListener !== "function") return null;
    var hasBoard = !!doc.getElementById("board-pane");
    var hasDm = !!doc.getElementById("dm-pane");
    if (!hasBoard || !hasDm) return null; // standalone page: nothing to route
    function current() {
      return paneForHash(
        typeof window !== "undefined" ? window.location.hash : "",
      );
    }
    show(doc, current());
    doc.addEventListener("hashchange", function () {
      show(doc, current());
    });
    // Same-page switches must not reload: the switcher links are real
    // path hrefs (standalone pages + bookmarks + pins depend on them), so
    // intercept them here and turn them into hash moves instead.
    doc.addEventListener("click", function (ev) {
      var t = ev.target && ev.target.closest
        ? ev.target.closest(".stx-cards-switcher__item")
        : null;
      if (!t) return;
      var href = t.getAttribute("href") || "";
      var which = href.slice(-6) === "#board" || href.slice(-6) === "/board"
        ? "board"
        : href.slice(-3) === "#dm" || href.slice(-3) === "/dm"
          ? "dm"
          : null;
      if (!which) return;
      ev.preventDefault();
      if (typeof window !== "undefined") {
        if (window.location.hash === "#" + which) show(doc, which);
        else window.location.hash = which;
      }
    });
    return { show: function (w) { return show(doc, w); } };
  }

  var _api = { paneForHash: paneForHash, applyPane: applyPane, mount: mount, repaintTitle: repaintTitle };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.appRouter = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        mount(document);
      });
    } else {
      mount(document);
    }
  }
})();
