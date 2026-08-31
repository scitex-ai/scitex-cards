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
    // Trailing messages rendered; grows on scroll-up. openThread resets it, but
    // an explicit default matters: `undefined` makes windowed() return the WHOLE
    // thread (length <= undefined is false, then slice(NaN)), which is safe but
    // silently un-does the fix. See chat_window.js.
    windowSize: 60,
  };

  var $agentsPane = document.getElementById("agents");
  var $agents = document.getElementById("agent-list");
  var $agentFilter = document.getElementById("agent-filter");
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

  // WHO the operator can talk to — the roster list, the avatars, the browser-tab
  // unread count, and the poll that feeds all of it — lives in ChatAgents.
  // chat.js keeps the seams: a row click opens the thread (and closes the
  // mobile drawer), and the polled roster is handed to the new-message input's
  // datalist so the operator can START a conversation, not only reply.
  var agentsApi = window.ChatAgents
    ? window.ChatAgents.mount({
        listEl: $agents,
        state: state,
        el: el,
        shortTs: shortTs,
        apiBase: API_BASE,
        getJSON: getJSON,
        showError: showError,
        clearError: clearError,
        onRowClick: function (peer) {
          openThread(peer);
          closeDrawer();
        },
        onAgents: function (agents) {
          if (window.ChatNewDm) window.ChatNewDm.setAgents(agents);
        },
      })
    : null;

  // Thin wrapper so the three call sites (openThread, send confirmation,
  // forward notice) and the boot below keep their name.
  function refreshAgents() {
    if (agentsApi) agentsApi.refresh();
  }

  // The new-message input above the filter: hand it the SAME open+close a row
  // click runs, so "type a name, Enter" behaves exactly like "click a row" —
  // one opener for two entrances into the same thread.
  if (window.ChatNewDm)
    window.ChatNewDm.setOpener(function (peer) {
      openThread(peer);
      closeDrawer();
    });

  // ---- thread pane -------------------------------------------------------

  // Attachment RENDERING lives in chat_attach.js, beside the upload that
  // produces the url — one module owns attachments end to end. `splitBody`
  // separates an `attachments/…` line from the prose; `nodeFor` builds the
  // <img>/<a> for one. Both are statics, so no mount ordering applies.
  var attachments = window.ChatAttach;

  // The delivery indicator: three dots filling sent -> queued -> read.
  // Optional by design: an older page without the module renders exactly as it
  // did before rather than guessing at a state it was not served.
  var receipts = window.ChatReceipts;

  function messageNode(m) {
    var mine = m.from === "operator";
    var wrap = el("div", "msg " + (mine ? "from-operator" : "from-agent"));
    // The id is what an ACTION addresses. Reacting to "the text in this
    // bubble" would attach the reaction to whatever is on screen; reacting to
    // an id survives a repaint.
    if (m.id) wrap.setAttribute("data-msg-id", String(m.id));
    var parts = attachments.splitBody(m.body);
    if (parts.text) wrap.appendChild(longtext.bubbleFor(parts.text, m));
    parts.files.forEach(function (rel) {
      wrap.appendChild(attachments.nodeFor(API_BASE, rel));
    });
    var meta = el("div", "meta", m.from + " · " + shortTs(m.ts));
    // The delivery indicator belongs to chat_receipts.js, which owns what each
    // step means; this file only says WHERE it goes. Into the META line on
    // purpose: that row already exists, so the track costs no vertical space
    // and cannot push the timestamp onto a second line on a phone.
    if (receipts) receipts.render(meta, m, state.receipts);
    wrap.appendChild(meta);
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
  /* The render window. Policy, arithmetic AND the scroll-up behaviour live in
   * chat_window.js; this only supplies the collaborators. Its header explains why
   * a window rather than content-visibility. */
  var win = (typeof window !== "undefined" && window.ChatWindow) || null;

  /* Rebuild the pane from a slice. Used by the scroll-up loader, which grew the
   * window at the FRONT — so the old fingerprints are a SUFFIX and planRender's
   * append path (prefix-only) cannot apply. */
  function repaintWindow(msgs) {
    $messages.textContent = "";
    msgs.forEach(function (m) {
      $messages.appendChild(messageNode(m));
    });
    state.rendered = diff.planRender(
      [],
      msgs,
      state.reactions,
      state.receipts,
    ).fingerprints;
  }

  var loader = win
    ? win.createScrollUpLoader($messages, state, repaintWindow)
    : null;
  if (loader) $messages.addEventListener("scroll", loader.onScroll);

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
        // Same rule, same reason: the plan's fingerprints read the receipts, so
        // a confirmation arriving on its own still repaints the bubble.
        state.receipts = data.receipts || null;
        state.messages = msgs;
        if (!msgs.length) {
          renderEmpty();
          return;
        }
        // Plan against the WINDOW, not the whole thread: state.messages stays
        // complete for everything else, only rendering is bounded.
        var view = win ? win.windowed(state.messages, state.windowSize) : msgs;
        applyPlan(
          diff.planRender(
            state.rendered,
            view,
            state.reactions,
            state.receipts,
          ),
          view,
        );
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
    // The window belongs to the OPEN THREAD, so it resets with the pane. Leaving
    // it grown would render 200 nodes of a thread the operator just switched to.
    state.windowSize = win ? win.WINDOW_INITIAL : Infinity;
    state.emptyShown = false;
    // Reactions and messages describe the pane that is being cleared, so they
    // clear with it. A stale map would paint the previous thread's chips onto
    // the first messages of this one.
    state.reactions = {};
    // null, not {}: an empty map would mean "served, and every message is
    // unknowable", which would paint a stale thread's bubbles with a state the
    // server never sent. null means "not served yet" and paints nothing.
    state.receipts = null;
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

  // ---- mobile drawer -----------------------------------------------------

  // State, inert-when-closed and the scrim pairing live in ChatDrawer — see that
  // module for the two defects it replaced (a closed drawer still in the tab
  // order, and a drawer/scrim desync that could strand the operator behind an
  // undismissable scrim). Panel is the NAV so the filter row travels with it.
  var drawerHost = { panel: $agentsPane, scrim: $scrim, trigger: $menuBtn };
  var drawer = window.ChatDrawer ? window.ChatDrawer.mount(drawerHost) : null;
  if (window.ChatFilter)
    window.ChatFilter.mount({ input: $agentFilter, list: $agents });

  function closeDrawer() {
    if (drawer) drawer.close();
  }

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
  // The peer the operator is talking to — one closure for the two mounts below.
  var getPeer = function () {
    return state.peer;
  };
  // Mounted AFTER `composer` exists, since it hands the composer its reset.
  if (window.ChatSend) {
    window.ChatSend.mount({
      form: $form,
      textarea: $body,
      send: $send,
      apiBase: API_BASE,
      composer: composer,
      getPeer: getPeer,
      onSent: function () {
        refreshThread();
        refreshAgents();
      },
      clearError: clearError,
      showError: showError,
    });
  }

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
      showNotice: showNotice,
      refreshThread: refreshThread,
      getPeer: getPeer,
      getMessages: function () {
        return state.messages;
      },
      getReactions: function () {
        return state.reactions;
      },
      getAgents: function () {
        return state.agents;
      },
      onForwarded: function (toPeer, count) {
        refreshAgents();
        var many =
          count > 1 ? count + " messages forwarded to " : "Forwarded to ";
        showNotice(many + toPeer + ".");
      },
    });
  }

  refreshAgents();
  state.timerList = setInterval(refreshAgents, LIST_POLL_MS);
})();

/* EOF */
