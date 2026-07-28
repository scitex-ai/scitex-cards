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
  var $attach = document.getElementById("compose-attach");
  var $file = document.getElementById("compose-file");
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
    var parts = splitAttachments(m.body);
    if (parts.text) wrap.appendChild(el("div", "bubble", parts.text));
    parts.files.forEach(function (rel) {
      wrap.appendChild(attachmentNode(rel));
    });
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

  // ---- attachments -------------------------------------------------------
  // Three ways in, one path out: picker, clipboard paste, drag-drop all call
  // uploadFiles, which appends the returned URL as its own line in the body.
  // Uploading BEFORE send means a failed upload never produces a message that
  // references a file that is not there.

  function uploadFiles(files) {
    var list = Array.prototype.slice.call(files || []).filter(Boolean);
    if (!list.length) return;
    clearError();
    list.forEach(function (file) {
      var form = new FormData();
      form.append("file", file);
      fetch(API_BASE + "/dm/upload", { method: "POST", body: form })
        .then(function (resp) {
          return resp.json().then(function (data) {
            if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
            return data;
          });
        })
        .then(function (data) {
          var sep = $body.value && !/\n$/.test($body.value) ? "\n" : "";
          $body.value += sep + data.url + "\n";
          $body.focus();
        })
        .catch(function (err) {
          showError("Upload failed: " + err.message);
        });
    });
  }

  if ($attach && $file) {
    $attach.addEventListener("click", function () {
      $file.click();
    });
    $file.addEventListener("change", function () {
      uploadFiles($file.files);
      $file.value = "";
    });
  }

  $body.addEventListener("paste", function (event) {
    var items = (event.clipboardData || {}).files;
    if (items && items.length) {
      event.preventDefault();
      uploadFiles(items);
    }
  });

  ["dragover", "drop"].forEach(function (name) {
    $body.addEventListener(name, function (event) {
      event.preventDefault();
      if (name === "drop") uploadFiles(event.dataTransfer.files);
    });
  });
  $form.addEventListener("submit", sendMessage);

  // ---- boot --------------------------------------------------------------

  refreshAgents();
  state.timerList = setInterval(refreshAgents, LIST_POLL_MS);
})();

/* EOF */
