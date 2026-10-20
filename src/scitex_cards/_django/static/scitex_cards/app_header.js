/* app_header.js — adopt the canonical SDK AppHeader slot for the leaf picker.
 *
 * Server-side, `_page_header.html` renders the mount stub
 * (`[data-stx-app-header]`) with the leaf's title + version as data
 * attributes and the server-rendered project picker inside it. The SDK's
 * prebuilt `app-header.js` module renders the canonical row (title span,
 * version badge, project slot, actions) into that stub. This file moves the
 * picker into the canonical slot so the row matches every other
 * project-scoped app (learn-from-stats: scitex-stats `adoptCanonicalHeaderSlot`).
 * When the SDK module failed to load, the picker stays where the server put
 * it rather than vanishing.
 *
 * Pure adopt() helper (node-tested below via module.exports) + DOM wiring
 * that short-circuits under node. No build step — served static.
 */
"use strict";

(function () {
  function adopt(root, api) {
    if (!root || typeof root.querySelector !== "function") return null;
    var picker = root.querySelector("[data-stx-project-picker]");
    if (!picker) return null;
    if (!api) return "left-in-place";
    if (typeof api.mount === "function") api.mount();
    var header =
      typeof api.get === "function" ? api.get() : null;
    if (header && header.projectSlot) {
      header.projectSlot.appendChild(picker);
      return "adopted";
    }
    return "left-in-place";
  }

  function run(doc) {
    if (!doc) return null;
    var api =
      typeof window !== "undefined" ? window.stxAppHeader : undefined;
    return adopt(doc, api);
  }

  var _api = { adopt: adopt, run: run };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.appHeaderAdopt = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        run(document);
      });
    } else {
      run(document);
    }
  }
})();
