/* dmPane.js — DM/chat LEFT pane on the board page.
 *
 * Operator layout directive 2026-10-19: DM/chat left, Board right, DM open
 * by DEFAULT (board_v3.html renders the `<aside id="dm-pane">` shell; this
 * file fills it). Frontend only — GET/POST the existing `dm/*` endpoints
 * (see `handlers/dm.py`), no backend change.
 *
 * Contract: fail-loud (a failed send keeps the draft and names the error
 * IN the pane), store-sourced (no parallel state, no invented peers), and
 * no innerHTML with server data (everything lands via `textContent`).
 *
 * PURE HELPERS + one DOM init, no globals at load. Pure half is
 * `node --test`ed (tests/scitex_cards/test__dm_pane.js); browser publish is
 * `window.STX.dmPane` (same seam as searchQuery.js); the template calls
 * the global `toggleDmPane()` from the pane's own buttons.
 */

(function () {
  "use strict";

  /** Polling cadence — same 30s as the other fleet surfaces (CI pills,
   * timing, timeline auto-refresh). Keeps the operator's "what just
   * changed" cognitive load uniform. */
  var DM_POLL_MS = 30000;

  var STORE_OPEN_KEY = "stx-cards-dm-open", STORE_PEER_KEY = "stx-cards-dm-peer";

  /** DEFAULT OPEN. The directive wants DM on open, so the absence of a
   * stored value (first visit, wiped storage) opens the pane — only an
   * explicit "0" closes it. */
  function dmDefaultOpen(stored) {
    return stored !== "0";
  }

  /** `?peer=` selects a thread (and opens the pane with it); anything else
   * selects nothing. Pure so the URL contract is test-pinned. */
  function dmPeerFromUrl(search) {
    try {
      var peer = new URLSearchParams(search || "").get("peer");
      return peer ? peer : null;
    } catch (e) {
      return null;
    }
  }

  /** `?dm=0` (or `closed`) closes the pane; `?dm=1` (or `open`) opens it;
   * absent or unrecognized leaves the stored/default state alone. */
  function dmClosedFromUrl(search) {
    try {
      var dm = new URLSearchParams(search || "").get("dm");
      if (dm === "0" || dm === "closed") return true;
      if (dm === "1" || dm === "open") return false;
      return null;
    } catch (e) {
      return null;
    }
  }

  function dmThreadsUrl(apiBase) {
    return apiBase + "/dm/threads";
  }

  function dmThreadUrl(apiBase, peer, markRead) {
    var url =
      apiBase + "/dm/thread/" + encodeURIComponent(peer);
    return markRead ? url + "?mark_read=1" : url;
  }

  function dmSendUrl(apiBase, peer) {
    return apiBase + "/dm/thread/" + encodeURIComponent(peer);
  }

  /** Author of a DM record. The store merges typed columns over the source
   * payload, so read defensively: never crash the pane on a record shape
   * this bundle has not seen. */
  function dmAuthor(m) {
    var a =
      (typeof m.author === "string" && m.author) ||
      (typeof m.from === "string" && m.from) ||
      (typeof m.sender === "string" && m.sender) ||
      "";
    return a.trim() || "unknown";
  }

  /** Body of a DM record, same defensive read. */
  function dmBody(m) {
    return (
      (typeof m.body === "string" && m.body) ||
      (typeof m.text === "string" && m.text) ||
      ""
    );
  }

  /** Timestamp of a DM record, falling back to "" (rendered as-is). */
  function dmTs(m) {
    return (
      (typeof m.ts === "string" && m.ts) ||
      (typeof m.created_at === "string" && m.created_at) ||
      ""
    );
  }

  /** Format a UTC ISO timestamp for the message metadata row. Falls back
   * to the raw string on a parse failure so a malformed row never takes
   * the pane down. */
  function formatDmTs(ts) {
    if (!ts) return "";
    var d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    try {
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return ts;
    }
  }

  /** Sum of per-agent unread counters for the rail badge. */
  function dmUnreadTotal(agents) {
    var total = 0;
    for (var i = 0; i < agents.length; i++) {
      var u = agents[i] && agents[i].unread;
      if (typeof u === "number" && u > 0) total += u;
    }
    return total;
  }

  /* -- DOM init (browser only; everything above is pure) ---------------- */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  /** Mount-aware API base. `window.API_BASE` is rendered by the template and
   * is "" at a root mount — empty is a VALID base meaning "this origin".
   * Only a MISSING marker (undefined) is the integration bug worth naming
   * (same rule as chat.js). Null when unavailable. */
  function apiBase() {
    if (typeof window === "undefined") return null;
    if (!("API_BASE" in window)) return null;
    return String(window.API_BASE || "").replace(/\/+$/, "");
  }

  function readStored(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function writeStored(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (e) {
      /* quota / unavailable — non-fatal */
    }
  }

  function setPaneOpen(open) {
    var pane = document.getElementById("dm-pane");
    if (pane) pane.classList.toggle("collapsed", !open);
    var toggles = document.querySelectorAll(
      '[data-dm-toggle][aria-expanded]',
    );
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].setAttribute("aria-expanded", open ? "true" : "false");
    }
    writeStored(STORE_OPEN_KEY, open ? "1" : "0");
    try {
      var params = new URLSearchParams(window.location.search);
      if (!open) params.set("dm", "0");
      else params.delete("dm");
      var qs = params.toString();
      var url = qs
        ? window.location.pathname + "?" + qs
        : window.location.pathname;
      window.history.replaceState(window.history.state, "", url);
    } catch (e) {
      /* non-fatal */
    }
  }

  function isPaneOpen() {
    var pane = document.getElementById("dm-pane");
    return !pane || !pane.classList.contains("collapsed");
  }

  /** Global the template's buttons call (same pattern as toggleDetailsPane).
   * Collapsing shrinks the pane to its rail — the rail button re-opens it,
   * so the control is never stranded on a closed pane. */
  function toggleDmPane() {
    setPaneOpen(!isPaneOpen());
  }

  function renderThreads(st, listBox) {
    while (listBox.firstChild) listBox.removeChild(listBox.firstChild);
    if (st.threadsLoading) {
      listBox.appendChild(
        el("p", "dm-pane__state", "Loading conversations…"),
      );
      return;
    }
    if (!st.agents.length) {
      listBox.appendChild(
        el("p", "dm-pane__state", st.error || "No conversations yet."),
      );
      return;
    }
    var ul = el("ul", "dm-pane__agents");
    ul.setAttribute("aria-label", "Conversations");
    for (var i = 0; i < st.agents.length; i++) {
      var a = st.agents[i];
      var li = document.createElement("li");
      var btn = el("button", "dm-pane__agent", null);
      btn.type = "button";
      if (a.name === st.peer) btn.classList.add("dm-pane__agent--on");
      btn.setAttribute("aria-pressed", a.name === st.peer ? "true" : "false");
      if (a.last_body) btn.title = a.last_body;
      (function (name) {
        btn.addEventListener("click", function () {
          selectPeer(st, name);
        });
      })(a.name);
      btn.appendChild(el("span", "dm-pane__agent-name", a.name));
      if (typeof a.unread === "number" && a.unread > 0) {
        var badge = el("span", "dm-pane__unread", String(a.unread));
        badge.setAttribute("aria-label", a.unread + " unread");
        btn.appendChild(badge);
      }
      li.appendChild(btn);
      ul.appendChild(li);
    }
    listBox.appendChild(ul);
  }

  function renderThread(st, threadBox) {
    while (threadBox.firstChild) threadBox.removeChild(threadBox.firstChild);
    if (!st.peer) {
      if (!st.threadsLoading && st.agents.length) {
        threadBox.appendChild(
          el("p", "dm-pane__state", "Select a conversation to read and reply."),
        );
      }
      return;
    }
    threadBox.appendChild(el("h3", "dm-pane__peer", st.peer));
    if (st.threadLoading) {
      threadBox.appendChild(el("p", "dm-pane__state", "Loading thread…"));
      return;
    }
    var ul = el("ul", "dm-pane__msgs");
    ul.setAttribute("aria-label", "Thread with " + st.peer);
    if (!st.messages.length) {
      var empty = document.createElement("li");
      empty.className = "dm-pane__state";
      empty.textContent = "No messages yet.";
      ul.appendChild(empty);
    }
    for (var i = 0; i < st.messages.length; i++) {
      var m = st.messages[i] || {};
      var li = el("li", "dm-pane__msg", null);
      var head = el("span", "dm-pane__msg-head", null);
      head.appendChild(el("span", "dm-pane__msg-author", dmAuthor(m)));
      var time = document.createElement("time");
      time.className = "dm-pane__msg-ts";
      time.textContent = formatDmTs(dmTs(m));
      head.appendChild(time);
      li.appendChild(head);
      li.appendChild(el("span", "dm-pane__msg-body", dmBody(m)));
      ul.appendChild(li);
    }
    threadBox.appendChild(ul);
    if (st.error) {
      var err = el("p", "dm-pane__error", st.error);
      err.setAttribute("role", "alert");
      threadBox.appendChild(err);
    }
    var compose = el("div", "dm-pane__compose", null);
    var area = document.createElement("textarea");
    area.className = "dm-pane__input";
    area.rows = 2;
    area.placeholder = "Message " + st.peer + "…";
    area.setAttribute("aria-label", "Message " + st.peer);
    area.disabled = st.sending;
    compose.appendChild(area);
    var send = el("button", "dm-pane__send", st.sending ? "…" : "Send");
    send.type = "button";
    send.disabled = true;
    area.addEventListener("input", function () {
      send.disabled = !area.value.trim() || st.sending;
    });
    area.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        if (!send.disabled) send.click();
      }
    });
    (function (box, peer) {
      send.addEventListener("click", function () {
        sendMessage(st, box, peer, area.value);
      });
    })(threadBox, st.peer);
    compose.appendChild(send);
    threadBox.appendChild(compose);
  }

  function refresh(st) {
    var listBox = document.getElementById("dm-threads");
    var threadBox = document.getElementById("dm-thread");
    if (listBox) renderThreads(st, listBox);
    if (threadBox) renderThread(st, threadBox);
    var railCount = document.getElementById("dm-rail-count");
    if (railCount) {
      var total = dmUnreadTotal(st.agents);
      railCount.textContent = total > 0 ? String(total) : "";
      railCount.hidden = total === 0;
    }
  }

  function loadThreads(st, base) {
    return fetch(dmThreadsUrl(base), { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("threads " + r.status);
        return r.json();
      })
      .then(function (data) {
        st.agents = (data && data.agents) || [];
        st.threadsLoading = false;
        st.error = st.peer ? st.error : null;
        refresh(st);
      })
      .catch(function (e) {
        st.threadsLoading = false;
        st.error = (e && e.message) || "threads failed";
        refresh(st);
      });
  }

  function loadThread(st, base) {
    if (!st.peer) return Promise.resolve();
    st.threadLoading = true;
    refresh(st);
    return fetch(dmThreadUrl(base, st.peer, true), {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("thread " + r.status);
        return r.json();
      })
      .then(function (data) {
        st.messages = (data && data.messages) || [];
        st.threadLoading = false;
        st.error = null;
        refresh(st);
        return loadThreads(st, base);
      })
      .catch(function (e) {
        st.threadLoading = false;
        st.error = (e && e.message) || "thread failed";
        refresh(st);
      });
  }

  function selectPeer(st, peer) {
    st.peer = peer;
    st.messages = [];
    st.error = null;
    writeStored(STORE_PEER_KEY, peer);
    try {
      var params = new URLSearchParams(window.location.search);
      params.set("peer", peer);
      params.delete("dm");
      var qs = params.toString();
      window.history.replaceState(
        window.history.state,
        "",
        window.location.pathname + (qs ? "?" + qs : ""),
      );
    } catch (e) {
      /* non-fatal */
    }
    setPaneOpen(true);
    refresh(st);
    /* Gesture-only scroll: the opening thread comes into view; the 30s poll
     * re-renders must never yank the operator's scroll. */
    var tbox = document.getElementById("dm-thread");
    if (tbox && typeof tbox.scrollIntoView === "function") {
      try {
        tbox.scrollIntoView({ block: "start" });
      } catch (e) {
        /* non-fatal */
      }
    }
    void loadThread(st, apiBase());
  }

  function sendMessage(st, threadBox, peer, body) {
    var text = (body || "").trim();
    if (!peer || !text || st.sending) return;
    st.sending = true;
    refresh(st);
    fetch(dmSendUrl(apiBase(), peer), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("send " + r.status);
        return r.json();
      })
      .then(function (data) {
        st.sending = false;
        st.error = null;
        if (data && data.message) st.messages = st.messages.concat([data.message]);
        refresh(st);
        var area = threadBox.querySelector(".dm-pane__input");
        if (area) {
          area.value = "";
          area.focus();
        }
        var btn = threadBox.querySelector(".dm-pane__send");
        if (btn) btn.disabled = true;
      })
      .catch(function (e) {
        /* Fail-loud AND lossless: name the failure, restore the draft from
         * the closure `text` so nothing is lost. */
        st.sending = false;
        st.error = "Send failed (" + ((e && e.message) || "network") + ") — draft kept, retry.";
        refresh(st);
        var area = threadBox.querySelector(".dm-pane__input");
        if (area) area.value = text;
      });
  }

  function initDmPane() {
    var pane = document.getElementById("dm-pane");
    if (!pane) return;
    var base = apiBase();
    var st = { peer: null, agents: [], messages: [],
      threadsLoading: true, threadLoading: false,
      sending: false, error: null };
    if (base === null) {
      var disabled = document.getElementById("dm-threads");
      if (disabled) {
        disabled.appendChild(
          el("p", "dm-pane__state", "DM unavailable: no API base on this page."),
        );
      }
      return;
    }
    /* Open state: URL wins over storage wins over the DEFAULT (open). */
    var urlClosed = dmClosedFromUrl(window.location.search);
    var open =
      urlClosed !== null ? !urlClosed : dmDefaultOpen(readStored(STORE_OPEN_KEY));
    var peer = dmPeerFromUrl(window.location.search) || readStored(STORE_PEER_KEY);
    if (peer) open = true;
    st.peer = peer;
    setPaneOpen(open);
    refresh(st);
    void loadThreads(st, base).then(function () {
      if (st.peer) void loadThread(st, base);
    });
    window.setInterval(function () {
      void loadThreads(st, base);
    }, DM_POLL_MS);
    window.setInterval(function () {
      if (st.peer && isPaneOpen()) void loadThread(st, base);
    }, DM_POLL_MS);
  }

  var dmPaneApi = {
    DM_POLL_MS: DM_POLL_MS,
    dmDefaultOpen: dmDefaultOpen,
    dmPeerFromUrl: dmPeerFromUrl,
    dmClosedFromUrl: dmClosedFromUrl,
    dmThreadsUrl: dmThreadsUrl,
    dmThreadUrl: dmThreadUrl,
    dmSendUrl: dmSendUrl,
    dmAuthor: dmAuthor,
    dmBody: dmBody,
    dmTs: dmTs,
    formatDmTs: formatDmTs,
    dmUnreadTotal: dmUnreadTotal,
    toggleDmPane: toggleDmPane,
    initDmPane: initDmPane,
  };

  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.dmPane = dmPaneApi;
  }
  if (typeof window !== "undefined") {
    window.toggleDmPane = toggleDmPane;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initDmPane);
    } else {
      initDmPane();
    }
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = dmPaneApi;
  }
})();
