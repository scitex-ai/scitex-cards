/* DM page panes: phone master-detail and the no-conversations state.
 *
 * Tapping a conversation adds `dm-thread-open` to #cards-dm-app (chat_panes.css
 * then shows the thread instead of the list on a phone); the back arrow and the
 * browser's own Back remove it. `dm-no-agents` mirrors chat.js's empty list so
 * the page can explain DMs instead of pointing at a list that is not there.
 * Plain browser JS, no dependencies; loads after chat.js.
 */
(function () {
  "use strict";

  var app = document.getElementById("cards-dm-app");
  var list = document.getElementById("agent-list");
  var back = document.getElementById("thread-back");
  if (!app || !list || !back) return;

  // The back arrow is only displayed in the phone layout, so its computed
  // display says which layout is active without repeating the breakpoint.
  function phoneLayout() {
    return window.getComputedStyle(back).display !== "none";
  }

  function isThreadEntry(state) {
    return !!(state && state.dmThread);
  }

  list.addEventListener("click", function (event) {
    if (!event.target.closest(".agent")) return;
    if (app.classList.contains("dm-thread-open")) return;
    app.classList.add("dm-thread-open");
    if (phoneLayout() && !isThreadEntry(history.state)) {
      history.pushState({ dmThread: true }, "");
    }
  });

  back.addEventListener("click", function () {
    if (isThreadEntry(history.state)) {
      history.back();
    } else {
      app.classList.remove("dm-thread-open");
    }
  });

  window.addEventListener("popstate", function (event) {
    if (!isThreadEntry(event.state)) app.classList.remove("dm-thread-open");
  });

  function syncEmpty() {
    app.classList.toggle("dm-no-agents", !!list.querySelector(".no-agents"));
  }
  new MutationObserver(syncEmpty).observe(list, { childList: true });
  syncEmpty();
})();

/* EOF */
