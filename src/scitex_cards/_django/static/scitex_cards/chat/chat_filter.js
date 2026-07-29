/* Fuzzy filter over the DM agent list.
 *
 * OPERATOR STANDING REQUEST, repeated: 「普通にあいまい検索でフィルタはいつも入れて
 * ください；scitex-ui にもなければいけない話です」 — a fuzzy-search filter belongs on
 * every list, and the matcher belongs in scitex-ui rather than being re-typed per
 * page. The board already consumes it (six filter <select>s are wrapped by
 * scitex-ui's Combobox); this page did not, and its list is the one that grows
 * without bound — every agent the fleet has ever registered, one flat column, no
 * way to narrow it.
 *
 * THE MATCHER IS NOT OURS. `STX.Combobox.fuzzyMatch` is scitex-ui's, exported as
 * a static beside the Combobox class for exactly this reason: consumers that want
 * a list narrowed but not a <select> replaced. Writing a second subsequence
 * matcher here would mean this page and the board disagree about what "matches"
 * means, and the operator would meet two different search behaviours in one app.
 * `substringMatch` below is a FALLBACK for a scitex-ui too old to export it, not
 * an alternative implementation — it is deliberately dumber, so a page running on
 * the fallback is visibly (not silently) degraded.
 *
 * WHY THIS FILTERS THE DOM AND NOT THE DATA. `renderAgents` in chat.js owns the
 * list and rebuilds it from scratch on every 5s poll. Filtering the array would
 * mean threading a query through that function, its caller and the poll timer;
 * hiding rows after the fact keeps ONE owner of the list markup and costs chat.js
 * (at its 512-line budget) nothing but the mount call. The MutationObserver is
 * what makes that honest: without it the filter would silently un-apply on the
 * next poll, which is the worst shape a filter can have — right until you look
 * away.
 *
 * Consumed two ways, hence the UMD-lite tail:
 *   - browser: <script src=chat_filter.js> -> window.ChatFilter
 *   - node (tests): require() -> module.exports
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
    root.ChatFilter = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* The fallback, used only when scitex-ui is absent or too old to export
   * `Combobox.fuzzyMatch`. Case-insensitive substring — strictly weaker than the
   * real matcher, on purpose. */
  function substringMatch(query, hay) {
    return (
      String(hay).toLowerCase().indexOf(String(query).toLowerCase()) !== -1
    );
  }

  /* Resolve the matcher at CALL time, not at load time: `chat_filter.js` and
   * scitex-ui's `combobox.js` are both deferred, and pinning the lookup at load
   * would freeze whichever happened to win the race. */
  function matcherFrom(scope) {
    var stx = scope && scope.STX;
    if (stx && stx.Combobox && typeof stx.Combobox.fuzzyMatch === "function") {
      return stx.Combobox.fuzzyMatch;
    }
    return substringMatch;
  }

  /* True when `name` should stay visible for `query`. An empty / whitespace
   * query matches everything — a filter with nothing typed in it must not hide
   * anything. */
  function matches(name, query, scope) {
    var q = String(query == null ? "" : query).trim();
    if (!q) return true;
    return !!matcherFrom(scope)(q.toLowerCase(), String(name).toLowerCase());
  }

  /* Pure list form, kept separate from the DOM walk so the suite can exercise
   * the real decision without a browser. */
  function filterNames(names, query, scope) {
    return (names || []).filter(function (name) {
      return matches(name, query, scope);
    });
  }

  /* Mount the filter.
   *
   * `input` and `list` are elements; the caller owns the lookup so this module
   * never guesses at ids (same contract as ChatDrawer). `itemSelector` and
   * `nameSelector` say how to find a row and read its name, so the module knows
   * nothing about what an "agent" is.
   *
   * Returns `{ apply, destroy }`; `apply` is exposed for the caller and for the
   * observer, and is safe to call at any time.
   */
  function mount(options) {
    var opts = options || {};
    var input = opts.input;
    var list = opts.list;
    if (!input || !list) return null;
    var scope = opts.scope || (typeof self !== "undefined" ? self : null);
    var itemSelector = opts.itemSelector || ".agent";
    var nameSelector = opts.nameSelector || ".name";
    var doc = list.ownerDocument;
    var emptyClass = "filter-empty";

    function apply() {
      var query = input.value;
      var rows = list.querySelectorAll(itemSelector);
      var shown = 0;
      for (var i = 0; i < rows.length; i++) {
        var nameEl = rows[i].querySelector(nameSelector);
        var name = nameEl ? nameEl.textContent : rows[i].textContent;
        var keep = matches(name, query, scope);
        rows[i].style.display = keep ? "" : "none";
        rows[i].setAttribute("aria-hidden", keep ? "false" : "true");
        if (keep) shown++;
      }

      /* A filter that hides everything and says nothing leaves a blank column,
       * and a blank column reads as a load that failed. Say which query emptied
       * it, so the operator knows the page is fine and the word is wrong. */
      var note = list.querySelector("." + emptyClass);
      if (rows.length && !shown) {
        if (!note) {
          note = doc.createElement("div");
          note.className = "empty " + emptyClass;
          list.appendChild(note);
        }
        note.textContent = 'No agent matches "' + String(query).trim() + '".';
      } else if (note) {
        note.remove();
      }
      return shown;
    }

    input.addEventListener("input", apply);

    /* Escape clears rather than blurs: the query is the thing in the way. */
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && input.value) {
        event.preventDefault();
        input.value = "";
        apply();
      }
    });

    /* The list is rebuilt wholesale on every poll, which throws away the
     * display:none we set. Re-apply on every rebuild. Only `childList` is
     * observed, so the style writes above cannot re-trigger this. */
    var observer = null;
    var Observer = scope && scope.MutationObserver;
    if (Observer) {
      observer = new Observer(function () {
        apply();
      });
      observer.observe(list, { childList: true });
    }

    apply();

    return {
      apply: apply,
      destroy: function () {
        if (observer) observer.disconnect();
        input.removeEventListener("input", apply);
      },
    };
  }

  return {
    matches: matches,
    filterNames: filterNames,
    substringMatch: substringMatch,
    mount: mount,
  };
});

/* EOF */
