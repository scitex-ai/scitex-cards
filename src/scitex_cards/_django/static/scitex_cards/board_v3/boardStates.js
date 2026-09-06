/* boardStates.js — turn a non-card answer into words a person can act on.
 *
 * Extracted from board_v3.html (see GITIGNORED/REFACTORING.md). Four functions
 * that share one job: when /graph does not hand back a card list, say WHY on the
 * board surface, in a shape someone can act on.
 *
 * WHY THIS FILE EXISTS AT ALL — a measured failure, 2026-08-17.
 * The operator reported the mounted board at scitex.ai/apps/cards/ as
 * 「何も表示されない。何もインタラクションできない」— nothing displayed, nothing
 * interactive. Loaded in a browser, the page was NOT silent: it was showing a
 * 90-word server diagnostic as one red line where the card list belongs. The
 * server had refused correctly and explained itself completely; the page turned
 * that into something that reads as "broken".
 *
 * So the lesson driving this file is narrower than "show errors". It is:
 *   A CORRECT DIAGNOSIS, RENDERED AS A WALL, IS INDISTINGUISHABLE FROM SILENCE.
 * Lead with the one line that names the cause. Keep the full text — it is the
 * remedy and it is often literally the command to run — but below the fold of
 * attention, not in place of an answer.
 *
 * Attached to `window` because board_v3.html's inline boot code calls these, and
 * a deferred external script has executed by the time that boot runs.
 */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[c];
    });
  }

  // Read a JSON body without throwing. A non-JSON error body is not an error
  // about the error; callers fall back to the status code alone.
  async function _readJsonBody(r) {
    try {
      return await r.json();
    } catch (_e) {
      return null;
    }
  }

  // Render a RECOGNIZED non-OK state; false → caller escalates loudly.
  //
  // A failed /graph is not always an ERROR — on the hub it can be a STATE:
  // signed-out (auth middleware 401s with {"error":"signed-out","login_url"}) or
  // no-active-project (tenancy middleware 404s with {"error","hint"}). Read the
  // body BEFORE giving up so the server's named reason drives a helpful panel.
  function renderLoadState(status, payload) {
    const cols = document.getElementById("columns");
    if (!cols) return false;
    if (status === 401 && payload && payload.error === "signed-out" && payload.login_url) {
      cols.innerHTML =
        `<div class="loading board-state board-state--signed-out">` +
        `<p>Signed out — the board needs a session.</p>` +
        `<p><a href="${escapeHtml(payload.login_url)}">Sign in to continue</a></p></div>`;
      return true;
    }
    const err = payload && payload.error ? String(payload.error) : "";
    if (status === 404 && payload && payload.hint && /^no active project/i.test(err)) {
      cols.innerHTML =
        `<div class="loading board-state board-state--no-project">` +
        `<p>No active project — the board shows your project&#39;s cards.</p>` +
        `<p><a href="${escapeHtml(payload.hint)}">Create or open a project</a></p></div>`;
      return true;
    }
    return false;
  }

  // Render an OK-but-EMPTY result as a NAMED state, never as a blank canvas.
  // Returns true when it painted (caller stops), false to render normally.
  //
  // THE TWO ZEROES ARE DIFFERENT AND THE SERVER ALREADY SEPARATES THEM.
  // `handlers/graph.py` sends `empty_store` and calls the distinction
  // load-bearing, because they need different sentences:
  //   empty_store true  — the database itself holds no cards. Nothing is wrong.
  //   empty_store false — the database HAS cards and none reached you. That is a
  //                       scoping answer, and it is the case that looks like a
  //                       bug and is not one.
  // Collapsing them into one "no cards" message throws away the only signal that
  // tells a viewer which situation they are in.
  function renderEmptyState(graph) {
    const nodes = (graph && graph.nodes) || [];
    if (nodes.length) return false;
    const cols = document.getElementById("columns");
    if (!cols) return false;
    if (graph && graph.empty_store) {
      cols.innerHTML =
        `<div class="loading board-state board-state--empty-store">` +
        `<p class="board-state__lead">No cards yet.</p>` +
        `<p>The board is connected and the store is empty — create a card to ` +
        `get started.</p></div>`;
    } else {
      cols.innerHTML =
        `<div class="loading board-state board-state--none-visible">` +
        `<p class="board-state__lead">No cards are visible to you.</p>` +
        `<p>The board reached the store and it holds cards, but none of them ` +
        `are scoped to this account or project.</p></div>`;
    }
    return true;
  }

  // The UNRECOGNIZED failure. Cause first, detail second, never a wall.
  //
  // The server's message is frequently long AND excellent — the store-resolution
  // refusal, for instance, names the offending default, recounts the 2026-08-09
  // incident that motivated it, and lists the precedence order to fix it. That
  // text must survive; what must change is that its FIRST line is the answer.
  // `_lead` takes the sentence up to the first period or em-dash so the headline
  // is the server's own words rather than a generic "something went wrong".
  // MEASURED, NOT GUESSED. The first version of this cut at the first " — ",
  // which on the real message "HTTP 500 — Cannot read the task store: …"
  // produced the headline "HTTP 500". That is precisely the uninformative lead
  // this whole file exists to eliminate — a status code tells a person nothing
  // they can act on. Found by rendering it and reading the screen.
  //
  // So: strip a leading "HTTP <code> — " transport prefix FIRST, then take the
  // first sentence of what the server actually said. The status is not lost;
  // it stays in the full text under the disclosure, where a status code
  // belongs.
  function _lead(message) {
    let m = String(message || "").trim();
    m = m.replace(/^HTTP\s+\d{3}\s*(?:—|-|:)?\s*/i, "");
    const cut = m.search(/(?:\s—\s|\.\s|:\s)/);
    const lead = cut > 0 ? m.slice(0, cut) : m;
    return lead || m || "unknown error";
  }

  function renderLoadError(err) {
    const cols = document.getElementById("columns");
    if (!cols) return false;
    const full = String((err && err.message) || err || "unknown error");
    cols.innerHTML =
      `<div class="loading board-state board-state--error">` +
      `<p class="board-state__lead">The board could not load its cards.</p>` +
      `<p class="board-state__cause">${escapeHtml(_lead(full))}</p>` +
      `<details class="board-state__detail"><summary>What the server said</summary>` +
      `<pre>${escapeHtml(full)}</pre></details></div>`;
    return true;
  }

  window._readJsonBody = _readJsonBody;
  window.renderLoadState = renderLoadState;
  window.renderEmptyState = renderEmptyState;
  window.renderLoadError = renderLoadError;
})();
