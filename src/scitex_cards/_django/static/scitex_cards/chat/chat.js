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

  /* How close to the bottom still counts as "at the bottom" when deciding
   * whether new messages follow down. A few px of rounding drift must not
   * strand the operator off the newest message. */
  var STICK_THRESHOLD_PX = 40;

  var diff = window.ChatDiff;

  var state = {
    peer: null, // currently open peer name, or null
    rendered: [], // fingerprints of the messages in the DOM, in order
    emptyShown: false, // the pane is currently the "no messages yet" hint
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
  var $errorBar = document.getElementById("error-bar");

  // ---- helpers -----------------------------------------------------------

  function showError(text) {
    $errorBar.textContent = text;
    $errorBar.style.display = "block";
  }

  function clearError() {
    $errorBar.style.display = "none";
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
        renderAgents(data.agents || []);
      })
      .catch(function (err) {
        showError("Agent list failed: " + err.message);
      });
  }

  // ---- thread pane -------------------------------------------------------

  function messageNode(m) {
    var mine = m.from === "operator";
    var wrap = el("div", "msg " + (mine ? "from-operator" : "from-agent"));
    wrap.appendChild(el("div", "bubble", m.body || ""));
    wrap.appendChild(el("div", "meta", m.from + " · " + shortTs(m.ts)));
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
        if (!msgs.length) {
          renderEmpty();
          return;
        }
        applyPlan(diff.planRender(state.rendered, msgs), msgs);
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

  function closeDrawer() {
    $agents.classList.remove("open");
    $scrim.classList.remove("open");
  }

  $menuBtn.addEventListener("click", function () {
    $agents.classList.toggle("open");
    $scrim.classList.toggle("open");
  });
  $scrim.addEventListener("click", closeDrawer);

  // Enter sends; Shift+Enter inserts a newline (phone keyboards send via
  // the button anyway — this is for desktop convenience).
  $body.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $form.requestSubmit();
    }
  });
  $form.addEventListener("submit", sendMessage);

  // ---- boot --------------------------------------------------------------

  refreshAgents();
  state.timerList = setInterval(refreshAgents, LIST_POLL_MS);
})();

/* EOF */
