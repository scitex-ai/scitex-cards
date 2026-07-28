/* Operator↔agent DM chat view — behavior for templates/scitex_cards/chat.html.
 *
 * Minimal slice (card fleet-agent-direct-message-board-pane-20260707):
 *   - GET  /dm/threads              -> agent list + unread badges (poll ~10s)
 *   - GET  /dm/thread/<peer>?mark_read=1 -> open thread (poll ~5s)
 *   - POST /dm/thread/<peer>        -> compose (from=operator)
 *
 * The thread pane repaints INCREMENTALLY: every poll is diffed against what
 * is already on screen, so an unchanged poll paints nothing, an arriving
 * message appends only itself, and reading back through history is not
 * interrupted every 5s. chat_diff.js holds that decision as pure, DOM-free
 * functions; this file owns the DOM and the network.
 *
 * Kept in a separate static file per the GUI's line-limit discipline
 * (js <512 lines). Plain browser JS, no build step, no dependencies.
 */
(function () {
  "use strict";

  /* Mount-aware API base (same contract as board_v3.html's API_BASE const).
   * The hub mounts this app under a sub-path (e.g. /apps/cards/), where
   * root-absolute fetches escape the mount and 404. chat.html ALWAYS renders
   * the include root on <body data-api-base> ("/" standalone, "/apps/cards/"
   * on the hub); trailing slashes are stripped so a root mount yields "" and
   * every call below stays "/dm/…"-shaped. A missing marker is an
   * INTEGRATION BUG (a template that forgot to set it), so it throws loudly
   * instead of silently guessing a root mount that would 404 on the hub. */
  var apiBaseRaw = document.body
    ? document.body.getAttribute("data-api-base")
    : null;
  if (apiBaseRaw === null) {
    throw new Error(
      'chat.js: <body data-api-base="…"> is missing — the page template ' +
        'must always set it ("/" at a root mount); refusing to guess the ' +
        "mount root.",
    );
  }
  var API_BASE = apiBaseRaw.replace(/\/+$/, "");
  /* Mirror onto window for parity with board_v3 (external scripts read it). */
  window.API_BASE = API_BASE;

  var THREAD_POLL_MS = 5000;
  var LIST_POLL_MS = 10000;

  /* How long a transient confirmation stays on screen. */
  var NOTICE_MS = 2500;

  /* How close to the bottom still counts as "at the bottom" when deciding
   * whether new messages follow down. A few px of rounding drift must not
   * strand the operator off the newest message. */
  var STICK_THRESHOLD_PX = 40;

  var diff = window.ChatDiff;
  /* Bubble construction — a long body clamps instead of filling the screen. */
  var longtext = window.ChatLongText;
  /* The message-action module (Reply / Copy / React / Forward + the reaction
   * chips). Assigned at boot; messageNode asks it where chips go. */
  var menu = null;

  /* This page IS the operator's side of the DM board — every POST it makes is
   * attributed to the operator server-side — so the operator is who a reaction
   * chip should light up for. */
  var VIEWER = "operator";

  var state = {
    peer: null, // currently open peer name, or null
    rendered: [], // fingerprints of the messages in the DOM, in order
    emptyShown: false, // the pane is currently the "no messages yet" hint
    messages: [], // last painted message records (the menu addresses these)
    reactions: {}, // {message_id: {emoji: [actors]}} from the last poll
    agents: [], // last agent list, reused by the forward picker
    timerThread: null,
    timerList: null,
  };

  var $agents = document.getElementById("agents");
  var $scrim = document.getElementById("scrim");
  var $menuBtn = document.getElementById("menu-btn");
  var $title = document.getElementById("thread-title");
  var $messages = document.getElementById("messages");
  var $form = document.getElementById("compose");
  var $body = document.getElementById("compose-body");
  var $send = document.getElementById("compose-send");
  var $attach = document.getElementById("compose-attach");
  var $file = document.getElementById("compose-file");
  var $errorBar = document.getElementById("error-bar");

  // ---- helpers -----------------------------------------------------------

  function showError(text) {
    $errorBar.classList.remove("ok");
    $errorBar.textContent = text;
    $errorBar.style.display = "block";
  }

  function clearError() {
    $errorBar.style.display = "none";
    $errorBar.classList.remove("ok");
  }

  /* Same bar, non-alarming colour. A forward lands in ANOTHER thread, so the
   * operator sees nothing happen in the one they are looking at — without a
   * confirmation the only honest read of a success is "the menu closed", which
   * is indistinguishable from a no-op. Reporting it in the RED bar would be
   * the opposite lie. */
  function showNotice(text) {
    $errorBar.textContent = text;
    $errorBar.classList.add("ok");
    $errorBar.style.display = "block";
    setTimeout(clearError, NOTICE_MS);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* Stamp on the viewer's own clock. The formatter lives in the DOM-free
   * ChatDiff module so node can exercise the REAL file — chat.js touches the
   * DOM and cannot be required under the JS test harness, so a pure function
   * kept here is a pure function nothing tests.
   *
   * What was here before sliced the ISO string and called the result
   * "local-enough": it was not local at all, it was UTC digits under a local
   * label. That is how a 20:39Z stamp read as an evening message to an operator
   * whose clock said 05:39 the next morning.
   */
  function shortTs(ts) {
    return diff.shortTs(ts);
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(
      function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status + " on " + url);
        return resp.json();
      },
    );
  }

  // ---- agent list --------------------------------------------------------

  // Deterministic per-agent avatar: hue from a stable name hash, initials
  // from the name's distinctive words (the shared "scitex-" prefix carries
  // no identity, so it is stripped before initials are taken).
  function avatarFor(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) {
      hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
    }
    var words = name
      .replace(/^scitex-/, "")
      .split(/[-_]+/)
      .filter(Boolean);
    var initials = words
      .slice(0, 2)
      .map(function (w) {
        return w.charAt(0).toUpperCase();
      })
      .join("");
    var av = el("span", "avatar", initials || "?");
    av.style.background = "hsl(" + (hash % 360) + ", 55%, 42%)";
    return av;
  }

  function renderAgents(agents) {
    // The list fully rebuilds each poll; keep the operator's scroll position.
    var scrollTop = $agents.scrollTop;
    $agents.textContent = "";
    if (!agents.length) {
      $agents.appendChild(
        el("div", "empty", "No agents registered and no threads yet."),
      );
      return;
    }
    agents.forEach(function (a) {
      var item = el("div", "agent" + (a.name === state.peer ? " active" : ""));
      item.appendChild(avatarFor(a.name));
      var cols = el("div", "cols");
      var row1 = el("div", "row1");
      row1.appendChild(el("span", "name", a.name));
      if (a.unread > 0) row1.appendChild(el("span", "badge", String(a.unread)));
      cols.appendChild(row1);
      var preview = a.last_body
        ? shortTs(a.last_ts) + "  " + a.last_body
        : a.kind
          ? a.kind
          : "no messages yet";
      cols.appendChild(el("div", "preview", preview));
      item.appendChild(cols);
      item.addEventListener("click", function () {
        openThread(a.name);
        closeDrawer();
      });
      $agents.appendChild(item);
    });
    $agents.scrollTop = scrollTop;
  }

  function refreshAgents() {
    getJSON(API_BASE + "/dm/threads")
      .then(function (data) {
        clearError();
        state.agents = data.agents || [];
        renderAgents(state.agents);
      })
      .catch(function (err) {
        showError("Agent list failed: " + err.message);
      });
  }

  // ---- thread pane -------------------------------------------------------

  // An attachment is carried as its own line in the body: a relative URL under
  // `attachments/`. Deliberately NOT a new sidecar field — threads.json is the
  // DM store and widening its record mid-incident is the kind of change that
  // has cost this board data before. The body is already the source of truth,
  // so a line IS the reference, and an older client still shows something
  // meaningful (the path) instead of nothing.
  var IMAGE_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;

  function splitAttachments(body) {
    var lines = String(body || "").split("\n");
    var text = [];
    var files = [];
    lines.forEach(function (line) {
      var t = line.trim();
      if (t.indexOf("attachments/") === 0) files.push(t);
      else text.push(line);
    });
    return { text: text.join("\n").trim(), files: files };
  }

  function attachmentNode(relUrl) {
    var href = API_BASE + "/" + relUrl;
    var name = relUrl.split("/").pop();
    if (IMAGE_RE.test(name)) {
      var a = el("a", "att-img");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener";
      var img = document.createElement("img");
      img.src = href;
      img.alt = name;
      img.loading = "lazy";
      a.appendChild(img);
      return a;
    }
    var link = el("a", "att-file", "📎 " + name);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }

  function messageNode(m) {
    var mine = m.from === "operator";
    var wrap = el("div", "msg " + (mine ? "from-operator" : "from-agent"));
    // The id is what an ACTION addresses. Reacting to "the text in this
    // bubble" would attach the reaction to whatever is on screen; reacting to
    // an id survives a repaint.
    if (m.id) wrap.setAttribute("data-msg-id", String(m.id));
    var parts = splitAttachments(m.body);
    if (parts.text) wrap.appendChild(longtext.bubbleFor(parts.text, m));
    parts.files.forEach(function (rel) {
      wrap.appendChild(attachmentNode(rel));
    });
    wrap.appendChild(el("div", "meta", m.from + " · " + shortTs(m.ts)));
    // Reaction chips belong to the menu module (it owns every reaction write),
    // so this file only says WHERE they go, never what they are.
    if (menu) menu.renderReactions(wrap, m);
    return wrap;
  }

  function atBottom() {
    return diff.shouldStickToBottom(
      $messages.scrollTop,
      $messages.scrollHeight,
      $messages.clientHeight,
      STICK_THRESHOLD_PX,
    );
  }

  function showHint(text) {
    $messages.textContent = "";
    $messages.appendChild(el("div", "hint", text));
  }

  function renderEmpty() {
    if (state.emptyShown && !state.rendered.length) return; // already shown
    showHint("No messages yet — say hello below.");
    state.rendered = [];
    state.emptyShown = true;
  }

  /* Bring the pane in line with `messages` by the smallest edit that will
   * do, holding the operator's scroll position unless they were already at
   * the bottom. */
  function applyPlan(plan, messages) {
    if (plan.mode === "noop") return;

    // Measure BEFORE mutating — afterwards the heights have already moved.
    var stick = atBottom();
    var prevTop = $messages.scrollTop;

    if (plan.mode === "rebuild") {
      $messages.textContent = "";
      messages.forEach(function (m) {
        $messages.appendChild(messageNode(m));
      });
    } else {
      // append: the pane may still hold a hint ("Loading…" on open, or the
      // empty-thread hint before the first message lands).
      if (state.emptyShown || !state.rendered.length)
        $messages.textContent = "";
      plan.added.forEach(function (m) {
        $messages.appendChild(messageNode(m));
      });
    }

    state.rendered = plan.fingerprints;
    state.emptyShown = false;

    if (stick) {
      $messages.scrollTop = $messages.scrollHeight;
    } else if (plan.mode === "rebuild") {
      // A rebuild replaced every node, taking the scroll offset with it.
      // An append leaves everything above it untouched, so it needs no
      // restore.
      $messages.scrollTop = prevTop;
    }
  }

  function refreshThread() {
    if (!state.peer) return;
    var peer = state.peer;
    getJSON(
      API_BASE + "/dm/thread/" + encodeURIComponent(peer) + "?mark_read=1",
    )
      .then(function (data) {
        if (state.peer !== peer) return; // switched away mid-flight
        clearError();
        var msgs = data.messages || [];
        // Reactions must be in place BEFORE the plan is computed: the plan's
        // fingerprints read them, and messageNode paints them.
        state.reactions = data.reactions || {};
        state.messages = msgs;
        if (!msgs.length) {
          renderEmpty();
          return;
        }
        applyPlan(diff.planRender(state.rendered, msgs, state.reactions), msgs);
      })
      .catch(function (err) {
        showError("Thread failed: " + err.message);
      });
  }

  function openThread(peer) {
    state.peer = peer;
    // The pane is cleared just below, so the rendered set must be cleared
    // with it — the two describe one fact and must not drift apart.
    state.rendered = [];
    state.emptyShown = false;
    // Reactions and messages describe the pane that is being cleared, so they
    // clear with it. A stale map would paint the previous thread's chips onto
    // the first messages of this one.
    state.reactions = {};
    state.messages = [];
    $title.innerHTML = "";
    $title.appendChild(document.createTextNode("Thread with "));
    $title.appendChild(el("b", null, peer));
    $body.disabled = false;
    $send.disabled = false;
    showHint("Loading…");
    refreshThread();
    refreshAgents(); // repaint the active highlight + clear the badge
    if (state.timerThread) clearInterval(state.timerThread);
    state.timerThread = setInterval(refreshThread, THREAD_POLL_MS);
  }

  // ---- compose -----------------------------------------------------------

  function sendMessage(event) {
    event.preventDefault();
    if (!state.peer) return;
    var text = $body.value.trim();
    if (!text) return;
    $send.disabled = true;
    fetch(API_BASE + "/dm/thread/" + encodeURIComponent(state.peer), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              throw new Error(data.error || "HTTP " + resp.status);
            });
        }
        $body.value = "";
        // Clearing `value` from script fires no `input` event, so say so.
        if (composer) composer.reset();
        clearError();
        refreshThread();
        refreshAgents();
      })
      .catch(function (err) {
        showError("Send failed: " + err.message);
      })
      .then(function () {
        $send.disabled = false;
        $body.focus();
      });
  }

  // ---- mobile drawer -----------------------------------------------------

  // State, inert-when-closed and the scrim pairing all live in ChatDrawer —
  // see that module for the two defects this replaced (a closed drawer that
  // was still tabbable, and a drawer/scrim desync that could strand the
  // operator behind an undismissable scrim).
  var drawerHost = { panel: $agents, scrim: $scrim, trigger: $menuBtn };
  var drawer = window.ChatDrawer ? window.ChatDrawer.mount(drawerHost) : null;

  function closeDrawer() {
    if (drawer) drawer.close();
  }

  // Enter sends; Shift+Enter inserts a newline (phone keyboards send via
  // the button anyway — this is for desktop convenience).
  $body.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $form.requestSubmit();
    }
  });

  // Attachments (picker / paste / drag-drop) live in chat_attach.js.
  var attach = window.ChatAttach
    ? window.ChatAttach.mount({
        apiBase: API_BASE,
        composerEl: $body,
        attachEl: $attach,
        fileEl: $file,
        showError: showError,
        clearError: clearError,
      })
    : null;
  // Auto-grow + the offer to send an over-long draft through that SAME path.
  var composer = window.ChatCompose
    ? window.ChatCompose.mount({
        form: $form,
        textarea: $body,
        uploadOne: attach ? attach.uploadOne : null,
        showError: showError,
      })
    : null;
  $form.addEventListener("submit", sendMessage);

  // ---- boot --------------------------------------------------------------

  // The message context menu (Reply / Copy / React / Forward) lives in
  // chat_menu.js. It gets the seams this file owns and reaches back for
  // nothing else.
  if (window.ChatMenu) {
    menu = window.ChatMenu.mount({
      apiBase: API_BASE,
      viewer: VIEWER,
      messagesEl: $messages,
      composerEl: $body,
      showError: showError,
      refreshThread: refreshThread,
      getPeer: function () {
        return state.peer;
      },
      getMessages: function () {
        return state.messages;
      },
      getReactions: function () {
        return state.reactions;
      },
      getAgents: function () {
        return state.agents;
      },
      onForwarded: function (toPeer) {
        refreshAgents();
        showNotice("Forwarded to " + toPeer + ".");
      },
    });
  }

  refreshAgents();
  state.timerList = setInterval(refreshAgents, LIST_POLL_MS);
})();

/* EOF */
