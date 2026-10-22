/* Hide/unhide row actions for the DM agent list — the DOM half
 * (card cards-dm-spaces-crud-hide-20261022). Pure decisions live next door
 * in chat_visibility.js (node-tested); this file owns the DOM and the
 * network, mounted from chat.js with seams like ChatNew/ChatFilter.
 *
 * iOS pattern, one button per row:
 *   - touch: swipe left slides the row over the action beneath it;
 *   - desktop: the same action overlays the row's end on hover/focus;
 *   - Hidden disclosure at the list's end (collapsed by default) with an
 *     Unhide action per hidden row and the unread count in its label.
 *
 * Writes are optimistic: the list flips first, the POST confirms, and any
 * failure shows in the error bar and re-polls to reconcile — the list never
 * displays a state the server refused.
 *
 * Mounted with `{ apiBase, showError, refreshAgents, applyLocal }` and
 * returns `{ decorateRow, renderHiddenSection }`. `applyLocal(peer, hidden)`
 * is the caller's optimistic flip (patch + re-render); the next poll is the
 * server truth and clears it.
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
    root.ChatHide = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var visibility = null;
  if (typeof require === "function") {
    try {
      visibility = require("./chat_visibility.js");
    } catch (e) {
      visibility = null;
    }
  } else if (typeof self !== "undefined" && self.ChatVisibility) {
    visibility = self.ChatVisibility;
  } else if (typeof window !== "undefined" && window.ChatVisibility) {
    visibility = window.ChatVisibility;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function mount(options) {
    var opts = options || {};
    var apiBase = opts.apiBase || "";
    var showError = opts.showError || function () {};
    var refreshAgents = opts.refreshAgents || function () {};
    var applyLocal = opts.applyLocal || function () {};

    /* The disclosure persists across the 10s poll rebuilds — module state,
     * not list state, or it would snap shut four seconds after opening. */
    var hiddenOpen = false;
    /* The one currently slid-open row; opening another (or tapping anywhere
     * else) closes it — iOS closes the old row, it does not stack them. */
    var openWrap = null;

    function closeOpen() {
      if (openWrap) {
        openWrap.classList.remove("open");
        openWrap = null;
      }
    }

    function postHidden(peer, hidden) {
      applyLocal(peer, hidden);
      fetch(
        apiBase + "/dm/thread/" + encodeURIComponent(peer) + "/hidden",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hidden: hidden }),
        },
      )
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.json();
        })
        .then(function () {
          refreshAgents();
        })
        .catch(function (err) {
          showError(
            (hidden ? "Hide" : "Unhide") + " failed: " + err.message,
          );
          refreshAgents();
        });
    }

    /* Wire swipe + the action button onto one row. `onTap` is the caller's
     * existing row behaviour (open the thread); a tap on a slid-open row
     * closes it instead of navigating — same as iOS. */
    function decorateRow(wrap, item, agent, rowOpts) {
      var cfg = rowOpts || {};
      var hidden = !!cfg.hidden;
      var onTap = cfg.onTap || function () {};
      var btn = el(
        "button",
        "row-act " + (hidden ? "row-unhide" : "row-hide"),
        hidden ? "Unhide" : "Hide",
      );
      btn.type = "button";
      btn.setAttribute(
        "aria-label",
        (hidden ? "Unhide conversation with " : "Hide conversation with ") +
          agent.name,
      );
      btn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        closeOpen();
        postHidden(agent.name, !hidden);
      });
      wrap.insertBefore(btn, wrap.firstChild);

      /* Touch swipe: horizontal wins the gesture (vertical scrolls the
       * list untouched); past half the action snaps open, else shut. */
      var startX = 0;
      var startY = 0;
      var tracking = false;
      wrap.addEventListener(
        "touchstart",
        function (ev) {
          var t = ev.touches[0];
          startX = t.clientX;
          startY = t.clientY;
          tracking = true;
        },
        { passive: true },
      );
      wrap.addEventListener(
        "touchmove",
        function (ev) {
          if (!tracking) return;
          var t = ev.touches[0];
          var dx = t.clientX - startX;
          var dy = t.clientY - startY;
          if (Math.abs(dy) > Math.abs(dx)) {
            tracking = false;
            return;
          }
          if (dx < 0) {
            if (typeof ev.preventDefault === "function") ev.preventDefault();
            wrap.classList.add("open");
            if (openWrap && openWrap !== wrap)
              openWrap.classList.remove("open");
            openWrap = wrap;
          }
        },
        { passive: false },
      );
      wrap.addEventListener("touchend", function () {
        tracking = false;
      });
      item.addEventListener("click", function () {
        if (wrap.classList.contains("open")) {
          closeOpen();
          return;
        }
        closeOpen();
        onTap();
      });
      return wrap;
    }

    /* The Hidden disclosure + its rows. `hidden` is the hidden partition,
     * `buildRow` builds one hidden row (caller-owned markup, same as visible
     * rows); `rerender` re-runs the list render after the toggle flips. */
    function renderHiddenSection(container, hidden, buildRow, rerender) {
      var vis = visibility || {};
      var unread =
        typeof vis.hiddenUnread === "function"
          ? vis.hiddenUnread(hidden)
          : 0;
      var label =
        typeof vis.disclosureLabel === "function"
          ? vis.disclosureLabel(hidden.length, unread)
          : "Hidden (" + hidden.length + ")";
      var toggle = el("button", "hidden-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", hiddenOpen ? "true" : "false");
      var chev = el("span", "chev", "\u25b8");
      toggle.appendChild(chev);
      toggle.appendChild(el("span", null, label));
      if (unread > 0)
        toggle.appendChild(el("span", "count", String(unread)));
      else toggle.appendChild(el("span", "count", String(hidden.length)));
      toggle.addEventListener("click", function () {
        hiddenOpen = !hiddenOpen;
        rerender();
      });
      container.appendChild(toggle);
      if (!hiddenOpen) return;
      hidden.forEach(function (a) {
        container.appendChild(buildRow(a, true));
      });
    }

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeOpen();
    });

    return {
      decorateRow: decorateRow,
      renderHiddenSection: renderHiddenSection,
    };
  }

  return {
    mount: mount,
  };
});

/* EOF */
