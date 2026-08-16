/* WHO the operator can talk to — the agent roster.
 *
 * Everything about the left-hand list of the chat view lives here: the rows
 * (avatar, name, unread badge, preview line), the browser-tab unread count,
 * and the poll that feeds both. chat.js keeps the seams: a row click opens
 * the thread and closes the mobile drawer, and the polled roster is handed
 * out so the new-message input can offer it as a datalist.
 *
 * WHY THE POLL AND THE TAB SHARE ONE ARRAY: the chat_title.js mount lives
 * here because the tab and the list are one fact rendered twice, and neither
 * counts anything itself. Giving the title its own request would be a second
 * answer to "how many unread?" — do not.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: chat.js requires window.ChatAgents and mounts it with the
 *     collaborators — this module never self-mounts, because the row click
 *     is chat.js's openThread + closeDrawer, not a behaviour of the list
 *   - node (tests): require() -> module.exports (the pure helpers stay
 *     exercisable without a browser)
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ChatAgents = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Pure, node-testable: the one line under the name. The last message wins
   * (its timestamp plus body); else the registry's kind, if the row carries
   * one; else the honest "no messages yet" — a roster row the operator has
   * never talked to. */
  function previewFor(agent, shortTs) {
    if (agent.last_body) return shortTs(agent.last_ts) + "  " + agent.last_body;
    if (agent.kind) return agent.kind;
    return "no messages yet";
  }

  /* Set by mount(); every collaborator is handed over by chat.js so this
   * module never guesses at elements, fetches, or state on its own. */
  var bound = null;

  /* Deterministic per-agent avatar. The hash/initials logic is pure and lives in
   * chat_avatar.js so node can test it; this builds the element from the spec. */
  function avatarFor(name) {
    var spec =
      window.ChatAvatar || null
        ? window.ChatAvatar.avatarSpec(name)
        : { initials: "?", background: "transparent" };
    var av = bound.el("span", "avatar", spec.initials);
    av.style.background = spec.background;
    return av;
  }

  function render(agents) {
    // The list fully rebuilds each poll; keep the operator's scroll position.
    var scrollTop = bound.listEl.scrollTop;
    bound.listEl.textContent = "";
    if (!agents.length) {
      bound.listEl.appendChild(
        bound.el("div", "empty", "No agents registered and no threads yet."),
      );
      return;
    }
    agents.forEach(function (a) {
      var item = bound.el(
        "div",
        "agent" + (a.name === bound.state.peer ? " active" : ""),
      );
      item.appendChild(avatarFor(a.name));
      var cols = bound.el("div", "cols");
      var row1 = bound.el("div", "row1");
      row1.appendChild(bound.el("span", "name", a.name));
      if (a.unread > 0)
        row1.appendChild(bound.el("span", "badge", String(a.unread)));
      cols.appendChild(row1);
      cols.appendChild(
        bound.el("div", "preview", previewFor(a, bound.shortTs)),
      );
      item.appendChild(cols);
      item.addEventListener("click", function () {
        bound.onRowClick(a.name);
      });
      bound.listEl.appendChild(item);
    });
    bound.listEl.scrollTop = scrollTop;
  }

  function refresh() {
    bound
      .getJSON(bound.apiBase + "/dm/threads")
      .then(function (data) {
        bound.clearError();
        bound.state.agents = data.agents || [];
        render(bound.state.agents);
        if (typeof bound.onAgents === "function")
          bound.onAgents(bound.state.agents);
        if (bound.pageTitle) bound.pageTitle.update(bound.state.agents);
      })
      .catch(function (err) {
        bound.showError("Agent list failed: " + err.message);
      });
  }

  function mount(options) {
    var opts = options || {};
    if (!opts.listEl || !opts.state || typeof opts.el !== "function")
      return null;
    bound = opts;
    // The unread count in the BROWSER TAB (chat_title.js), moved in with this
    // module: the tab and the list below it are one fact rendered twice, and
    // neither counts anything itself.
    bound.pageTitle = window.ChatTitle ? window.ChatTitle.mount({}) : null;
    return { refresh: refresh };
  }

  return {
    previewFor: previewFor,
    mount: mount,
  };
});

/* EOF */
