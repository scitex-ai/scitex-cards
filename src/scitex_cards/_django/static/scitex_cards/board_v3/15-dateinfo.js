/* 15-dateinfo.js — date extraction + proximity helpers for the
 * scitex-cards GUI (board_v3).
 *
 * Extracted VERBATIM from board_v3.html's inline <script> (the C5 cluster)
 * as the first step of the template split
 * (card scitex-cards-gui-board-v3-template-split-20260717). Pure functions
 * of their arguments — no DOM, no shared globals — so `node` can require()
 * the SHIPPED file and test it for real, which the inline copy could never
 * be. Behaviour is unchanged; only the location moved.
 *
 * Chosen as the FIRST extraction precisely because it is the purest cluster:
 * nothing here reads STATE, touches the DOM, or is named by an inline
 * onclick=, so it is the safest possible pattern-setter for the extractions
 * that follow.
 *
 * Publishes window.STX.dateInfo. The one caller in the template
 * (passes(), the filter predicate) reaches it as window.STX.dateInfo.dateInfo
 * at CALL time — the same convention the other extracted modules
 * (timelinePack / stickyWall / matrix) use, and call-time lookup so script
 * load order does not matter.
 */
"use strict";

(function (global) {
  // Pulls every YYYY-MM-DD (also YYYY/MM/DD) out of a string. Module-scoped
  // regex with an explicit lastIndex reset before each scan, so the shared
  // /g statefulness cannot leak between calls.
  var _DATE_RX = /(20\d{2})[-\/](\d{1,2})[-\/](\d{1,2})/g;

  function _parseAllDates(title) {
    var out = [];
    var m;
    _DATE_RX.lastIndex = 0;
    while ((m = _DATE_RX.exec(title)) !== null) {
      var y = +m[1],
        mo = +m[2],
        d = +m[3];
      var dt = new Date(y, mo - 1, d);
      if (!isNaN(dt) && dt.getMonth() === mo - 1) out.push(dt);
    }
    return out;
  }

  // P4 PR3: scan a raw deadline string for an org repeater suffix like
  // " +1w" / " ++2m" and return a human-readable form ("every 1w") for the
  // date-pill label. Returns null when absent.
  function _extractRepeaterSuffix(rawDeadline) {
    if (!rawDeadline) return null;
    var m = String(rawDeadline).match(/\s\+\+?(\d+)([dwmy])$/);
    if (!m) return null;
    return "every " + m[1] + m[2];
  }

  function _firstRecurringDeadline(t) {
    var arr = Array.isArray(t.deadlines) ? t.deadlines : [];
    for (var i = 0; i < arr.length; i++) {
      var e = arr[i];
      if (typeof e === "string" && /\s\+\+?\d+[dwmy]$/.test(e)) {
        return e;
      }
    }
    return null;
  }

  // Pick the date a card's pill should show: the SERVER-COMPUTED
  // `deadline_next` over the raw `deadline` over the title-parsed date
  // (P4, lead approved 2026-06-12). Tasks without any of those fall through
  // to the _DATE_RX title scan; the NEXT future date wins, or the most recent
  // past one if all are past. Returns null when a card has no date at all.
  function dateInfo(t) {
    var now = new Date();
    now.setHours(0, 0, 0, 0);
    var next = t.deadline_next || t.deadline;
    if (next) {
      var dl = new Date(next);
      if (!isNaN(dl)) {
        dl.setHours(0, 0, 0, 0);
        var days = Math.round((dl - now) / 86400000);
        return {
          date: dl,
          daysFromNow: days,
          all: [dl],
          source: t.deadline_next ? "deadline_next" : "deadline",
          repeater:
            _extractRepeaterSuffix(t.deadline) ||
            _extractRepeaterSuffix(_firstRecurringDeadline(t)),
        };
      }
    }
    var title = (t.title || "") + " " + (t.task || "");
    var dates = _parseAllDates(title);
    if (dates.length === 0) return null;
    var future = dates
      .filter(function (d) {
        return d >= now;
      })
      .sort(function (a, b) {
        return a - b;
      });
    var pick = future.length
      ? future[0]
      : dates.sort(function (a, b) {
          return b - a;
        })[0];
    var pdays = Math.round((pick - now) / 86400000);
    return { date: pick, daysFromNow: pdays, all: dates, source: "title" };
  }


  /* fmtLocalTime(ts) — a stored UTC ISO stamp, rendered in the VIEWER's zone.
   *
   * The board stores and ships UTC. The detail panel used to print that
   * string raw, so an operator in JST read every timestamp NINE HOURS in the
   * past — the complaint that produced PR #330 (2026-07-18, the operator's
   * own patch, stranded unmergeable against the pre-rename tree until it was
   * re-implemented here).
   *
   * TWO BEHAVIOURS KEPT VERBATIM FROM HIS PATCH, both deliberate:
   *   - `hourCycle: "h23"`, NOT `hour12: false`. The latter renders midnight
   *     as "24:59" in some engines; h23 is the one that says 00:59.
   *   - an UNPARSEABLE value is returned AS GIVEN, never "Invalid Date". A
   *     raw stamp a reader can copy beats a string that says only that the
   *     formatter gave up.
   */
  function fmtLocalTime(ts) {
    if (!ts) return "—";
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    try {
      return d.toLocaleString(undefined, {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      });
    } catch (e) {
      return String(ts);
    }
  }

  var _api = {
    dateInfo: dateInfo,
    fmtLocalTime: fmtLocalTime,
    _parseAllDates: _parseAllDates,
    _extractRepeaterSuffix: _extractRepeaterSuffix,
    _firstRecurringDeadline: _firstRecurringDeadline,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.dateInfo = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
