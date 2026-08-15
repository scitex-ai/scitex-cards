/* "My Cards" phone page — the DOM and network half.
 *
 * Every DECISION lives in me_view.js, which is DOM-free and node-tested. This
 * file does the two things that need a browser: fetch `/mine` and paint the
 * result. Keeping the split honest is what makes the logic testable at all
 * (see me_view.js's header), so resist moving a rule down here because it is
 * "only a line".
 *
 * Plain browser JS, no build step, no dependencies (line-limit discipline:
 * js <512 lines).
 */
(function () {
  "use strict";

  var VIEW = window.MeView;

  /* The include root the page was served under ("/" standalone, "/apps/cards/"
   * on the hub). READ FROM THE MARKUP, NEVER GUESSED: the board has shipped
   * this bug twice (#556, #557) by hardcoding a root-absolute path that 404s
   * the moment the app is mounted under a sub-path. A missing marker is an
   * integration bug and says so, rather than silently guessing "/" and
   * half-working on exactly one deployment. */
  var API_BASE = document.body.getAttribute("data-api-base");

  var els = {
    summary: document.getElementById("summary"),
    viewer: document.getElementById("viewer"),
    list: document.getElementById("list"),
    state: document.getElementById("state"),
    closed: document.getElementById("closed-toggle"),
  };

  var showClosed = false;

  function text(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = value;
    return node;
  }

  /* textContent everywhere, innerHTML nowhere.
   *
   * Card titles are written by agents and by the operator and routinely carry
   * `<`, `&` and quotes. Building this list by string concatenation would make
   * every card title a script injection into the page that displays it, so the
   * DOM API does the escaping structurally instead of a helper that somebody
   * has to remember to call. */
  function cardNode(card, now) {
    var item = document.createElement("li");
    item.className = "card card--" + (card.status || "unknown");

    var link = document.createElement("a");
    link.className = "card__title";
    link.href = API_BASE + "board?card=" + encodeURIComponent(card.id || "");
    link.textContent = card.title || card.id || "(untitled)";
    item.appendChild(link);

    var meta = text("div", "card__meta");

    if (card.project) meta.appendChild(text("span", "chip", card.project));
    if (card.blocker) {
      meta.appendChild(text("span", "chip chip--blocker", card.blocker));
    }
    if (typeof card.priority === "number") {
      meta.appendChild(text("span", "chip", "P" + card.priority));
    }

    var deadline = VIEW.deadlineState(card, now);
    if (deadline) {
      var when = card.deadline_next || card.deadline;
      meta.appendChild(
        text("span", "chip chip--" + deadline, deadline + " · " + when),
      );
    }

    var seen = VIEW.relativeTime(card.last_activity, now);
    if (seen) meta.appendChild(text("span", "card__seen", seen));

    item.appendChild(meta);
    return item;
  }

  function renderCards(payload) {
    var now = Date.now();
    var sections = VIEW.planSections(payload.cards);
    els.list.textContent = "";

    sections.forEach(function (section) {
      var heading = text("h2", "section__title", section.label);
      heading.appendChild(text("span", "section__count", section.cards.length));
      els.list.appendChild(heading);

      var list = text("ul", "section__cards");
      section.cards.forEach(function (card) {
        list.appendChild(cardNode(card, now));
      });
      els.list.appendChild(list);
    });

    els.summary.textContent = VIEW.summarise(payload);
    els.viewer.textContent = (payload.viewer && payload.viewer.name) || "";
    els.state.textContent = "";
    els.state.hidden = true;
  }

  /* A refusal is a STATE THIS PAGE RENDERS, not an error it logs.
   *
   * "We do not know who you are" is the single most likely thing a first
   * visitor sees, and a console message would leave them looking at a blank
   * screen. The two reasons say different things — see me_view.refusalMessage,
   * which is where that distinction is tested. */
  function renderRefusal(body) {
    var message = VIEW.refusalMessage(body);
    els.list.textContent = "";
    els.summary.textContent = "";
    els.viewer.textContent = message.email || "";
    els.state.textContent = "";
    els.state.appendChild(text("h2", "state__title", message.title));
    els.state.appendChild(text("p", "state__detail", message.detail));
    els.state.hidden = false;
  }

  function renderFailure(detail) {
    /* A READ THAT FAILED IS NOT AN EMPTY BOARD. Painting "nothing on your
     * plate" over a failed fetch is the believable-empty-board failure this
     * repo already had once, in the 2026-07-29 outage: it looks healthy and
     * hides the fault. So the list is left alone and the fault is stated. */
    els.state.textContent = "";
    els.state.appendChild(
      text("h2", "state__title", "Could not load your cards"),
    );
    els.state.appendChild(text("p", "state__detail", detail));
    els.state.hidden = false;
  }

  function load() {
    if (!API_BASE && API_BASE !== "") {
      renderFailure(
        "This page was served without its API base, so it does not know " +
          "where to ask. That is a deployment bug, not something you can fix " +
          "from here.",
      );
      return;
    }
    var url = API_BASE + "me/cards" + (showClosed ? "?closed=1" : "");
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      })
      .then(function (result) {
        if (result.ok) {
          renderCards(result.body);
        } else if (result.status === 403) {
          renderRefusal(result.body);
        } else {
          renderFailure(
            (result.body && result.body.detail) ||
              "The board answered " + result.status + ".",
          );
        }
      })
      .catch(function (error) {
        renderFailure(String(error));
      });
  }

  if (els.closed) {
    els.closed.addEventListener("click", function () {
      showClosed = !showClosed;
      els.closed.setAttribute("aria-pressed", showClosed ? "true" : "false");
      els.closed.textContent = showClosed ? "Hide done" : "Show done";
      load();
    });
  }

  load();

  /* Refresh when the phone comes back to the page rather than on a timer.
   *
   * A poll running in a backgrounded tab spends a phone's battery and its
   * data allowance to repaint something nobody is looking at. The moment that
   * actually matters is when the screen comes back on, which is exactly what
   * this event reports. */
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) load();
  });
})();

/* EOF */
