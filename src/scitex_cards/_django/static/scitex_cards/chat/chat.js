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

  function shortTs(ts) {
    // "2026-07-07T09:15:02Z" -> "07-07 09:15" (local-enough for the floor).
    if (!ts) return "";
    var m = String(ts).match(/^\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2})/);
    return m ? m[1] + " " + m[2] : String(ts);
  }

  /* Mount-relative API base: chat.html injects window.SCITEX_CARDS_API_BASE
   * (server-derived — "/" standalone, "/<prefix>/" when a host mounts the
   * app under a prefix) before this deferred script runs. Joining onto it
   * keeps the /dm/* polls working under any mount instead of assuming the
   * site root. */
  function apiUrl(endpoint) {
    var base = window.SCITEX_CARDS_API_BASE || "/";
    return base + String(endpoint).replace(/^\/+/, "");
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

  function renderAgents(agents) {
    $agents.textContent = "";
    if (!agents.length) {
      $agents.appendChild(
        el("div", "empty", "No agents registered and no threads yet."),
      );
      return;
    }
    agents.forEach(function (a) {
      var item = el("div", "agent" + (a.name === state.peer ? " active" : ""));
      var row1 = el("div", "row1");
      row1.appendChild(el("span", "name", a.name));
      if (a.unread > 0) row1.appendChild(el("span", "badge", String(a.unread)));
      item.appendChild(row1);
      var preview = a.last_body
        ? shortTs(a.last_ts) + "  " + a.last_body
        : a.kind
          ? a.kind
          : "no messages yet";
      item.appendChild(el("div", "preview", preview));
      item.addEventListener("click", function () {
        openThread(a.name);
        closeDrawer();
      });
      $agents.appendChild(item);
    });
  }

  function refreshAgents() {
    getJSON(apiUrl("dm/threads"))
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
    getJSON(apiUrl("dm/thread/" + encodeURIComponent(peer) + "?mark_read=1"))
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
    fetch(apiUrl("dm/thread/" + encodeURIComponent(state.peer)), {
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
