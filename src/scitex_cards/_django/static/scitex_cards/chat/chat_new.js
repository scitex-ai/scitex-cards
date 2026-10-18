/* New-agent dialog (CCT 3853): buzz-search the Agents-app directory and jump
 * to the existing room — or an empty pane the first send turns into one.
 *
 * Module pattern mirrors chat_menu.js: the controller (chat.js) mounts this
 * with collaborators, so there is exactly ONE opener and ONE fetcher.
 * The agent list is NEVER kept here — every keystroke hits GET dm/agents,
 * which consumes the loose agent-directory port server-side.
 */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function mount(host) {
    var apiBase = host.apiBase || "";
    var openThread = host.openThread;
    var btn = document.getElementById("dm-new");
    if (!btn || typeof openThread !== "function") return null;

    var overlay = null;
    var input = null;
    var results = null;
    var timer = 0;

    function close() {
      if (overlay && overlay.parentNode)
        overlay.parentNode.removeChild(overlay);
      overlay = input = results = null;
      if (timer) {
        clearTimeout(timer);
        timer = 0;
      }
      btn.focus();
    }

    function pick(id) {
      close();
      openThread(id);
      // Same pairing as the drawer's own tap handler: the pane opens while
      // the mobile drawer stays shut behind it.
      if (host.closeDrawer) host.closeDrawer();
    }

    function render(list) {
      results.textContent = "";
      if (!list.length) {
        results.appendChild(el("div", "dm-new-empty", "No matching agent."));
        return;
      }
      list.forEach(function (a) {
        var item = el("button", "dm-new-item");
        item.type = "button";
        item.appendChild(el("span", "dm-new-name", a.name || a.id));
        var meta = (a.host ? a.host + " · " : "") + (a.status || "");
        if (a.has_thread) meta += (meta ? " · " : "") + "has room";
        if (meta) item.appendChild(el("span", "dm-new-meta", meta));
        item.addEventListener("click", function () {
          pick(a.id);
        });
        results.appendChild(item);
      });
    }

    function search(q) {
      fetch(apiBase + "/dm/agents?q=" + encodeURIComponent(q))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          if (results) render(data.agents || []);
        })
        .catch(function () {
          if (results) {
            results.textContent = "";
            results.appendChild(
              el("div", "dm-new-empty", "Agent search failed.")
            );
          }
        });
    }

    function onInput() {
      if (timer) clearTimeout(timer);
      var q = input.value;
      timer = setTimeout(function () {
        search(q);
      }, 150);
    }

    function open() {
      if (overlay) return;
      overlay = el("div", "dm-new-overlay");
      var box = el("div", "dm-new-box");
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-label", "New conversation");
      input = el("input", "dm-new-input");
      input.type = "search";
      input.placeholder = "Search agents…";
      input.setAttribute("aria-label", "Search agents");
      input.addEventListener("input", onInput);
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") close();
      });
      results = el("div", "dm-new-results");
      var hint = el(
        "div",
        "dm-new-hint",
        "Pick an agent to open its room — created on your first send."
      );
      box.appendChild(input);
      box.appendChild(results);
      box.appendChild(hint);
      overlay.appendChild(box);
      overlay.addEventListener("click", function (ev) {
        if (ev.target === overlay) close();
      });
      document.body.appendChild(overlay);
      input.focus();
      search("");
    }

    btn.addEventListener("click", open);
    return { open: open, close: close };
  }

  window.ChatNew = { mount: mount };
})();
