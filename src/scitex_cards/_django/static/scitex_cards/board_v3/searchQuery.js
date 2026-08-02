/* searchQuery.js — GitHub-style qualifier-syntax parser for the board
 * search input.
 *
 * Operator pain (TG 12315 / 12316, lead a2a 7dde227a, 2026-06-12): the
 * operator typed `project: paper-scitex-clew` into the board search bar
 * — i.e. they already expected GitHub-style `<key>:<value>` qualifier
 * syntax — and was surprised it fell through to the plain fuzzy match.
 * Obvious affordance gap.
 *
 * This module is a PURE PARSER + MATCHER — no DOM, no global state, no
 * fetch. The matcher is plugged into the board's existing `passFilter`
 * pipeline so the rest of the filter stack (status / blocker / hidden /
 * date) is untouched. Pure-fn shape also makes the parser unit-testable
 * via `node --test` (see ``tests/scitex_cards/test__search_query.js``).
 *
 * Supported qualifiers (mapped to the task store field of the same name,
 * plus a few aliases for the operator's mental model):
 *
 *   project: | repo:        →  task.project (with task.repo fallback)
 *                              substring (case-insensitive)
 *   agent:   | assignee:    →  task.agent (with task.assignee fallback)
 *                              substring (case-insensitive)
 *   status:                 →  task.status (case-insensitive exact;
 *                              unknown value flagged unknownValue → empty)
 *   kind:                   →  task.kind   (case-insensitive exact;
 *                              unknown value flagged unknownValue → empty)
 *   parent:                 →  task.parent (exact id match)
 *   scope:                  →  task.scope  (substring, case-insensitive)
 *   id:                     →  task.id     (substring, case-insensitive)
 *   priority:               →  task.priority — `1` exact, `<3`/`>=2` range
 *   host:                   →  task.host   (substring, case-insensitive)
 *
 * Multi-qualifier → AND-combined. Bare tokens (no colon) → free-text on
 * title + note + id + task + project + agent (the existing fzf-style
 * subsequence haystack the board already uses).
 *
 * Quoting: project:"paper scitex clew" allows spaces inside the value.
 * Tolerant of "project: paper-scitex-clew" (space after colon) — the
 * operator's exact photographed pattern. Trailing whitespace stripped.
 *
 * The parse output also carries a list of ``hints`` describing each
 * recognized vs unknown qualifier so the filterbar can render hint pills
 * (operator UX: "show me what you parsed"); see ``parseSearchQuery``.
 */
"use strict";

/* === Closed enums (mirror the Python _model.py validators) ================
 * Used by `status:` + `kind:` exact-match qualifiers. If the value is not
 * in the enum the parser flags `unknownValue: true` so the UI can grey the
 * hint pill + show a "did you mean" tooltip while STILL filtering the
 * list down to zero matches (intentional — operator sees "you typed
 * status:foo and there are 0 matches" instead of silently matching all). */
const VALID_STATUSES = [
  "goal",
  "pending",
  "in_progress",
  "blocked",
  "done",
  "deferred",
  "failed",
  // cancelled = closed-as-not-planned (terminal). Mirrors _model.py
  // VALID_STATUSES so `status:cancelled` is a recognized search qualifier.
  "cancelled",
];

const VALID_KINDS = ["task", "compute", "decision"];

/* === Qualifier dictionary =================================================
 * Map every accepted qualifier (incl. aliases) to a canonical key + a
 * match strategy. The UI uses `canonical` for the hint-pill label so two
 * qualifiers that mean the same thing (project / repo) collapse to one
 * pill. */
const QUALIFIERS = {
  project: {
    canonical: "project",
    strategy: "substring",
    fields: ["project", "repo"],
  },
  repo: {
    canonical: "project",
    strategy: "substring",
    fields: ["project", "repo"],
  },
  agent: {
    canonical: "agent",
    strategy: "substring",
    fields: ["agent", "assignee"],
  },
  assignee: {
    canonical: "agent",
    strategy: "substring",
    fields: ["agent", "assignee"],
  },
  status: {
    canonical: "status",
    strategy: "enum",
    fields: ["status"],
    enum: VALID_STATUSES,
  },
  kind: {
    canonical: "kind",
    strategy: "enum",
    fields: ["kind"],
    enum: VALID_KINDS,
  },
  parent: { canonical: "parent", strategy: "exact", fields: ["parent"] },
  scope: { canonical: "scope", strategy: "substring", fields: ["scope"] },
  id: { canonical: "id", strategy: "substring", fields: ["id"] },
  host: { canonical: "host", strategy: "substring", fields: ["host"] },
  priority: {
    canonical: "priority",
    strategy: "priority",
    fields: ["priority"],
  },
};

const KNOWN_QUALIFIER_NAMES = Object.keys(QUALIFIERS);

/* Tokenizer: split a raw search string into a list of either
 *   { qualifier: "project", value: "paper-scitex-clew", raw: "project:paper-…" }
 * or
 *   { text: "deadline" }
 * tokens. Whitespace-delimited; quoted values keep their spaces.
 *
 * Tolerates `project: paper-scitex-clew` (space after colon) per the
 * operator's photographed pattern — when a token ends in `:` we attach
 * the NEXT token as its value (greedily consuming a following quoted run).
 */
function tokenize(input) {
  const out = [];
  const s = String(input == null ? "" : input);
  let i = 0;
  const len = s.length;
  while (i < len) {
    // Skip whitespace.
    while (i < len && /\s/.test(s[i])) i++;
    if (i >= len) break;
    // Read one "raw token" — either a quoted run "…" or a non-whitespace
    // run. We do NOT split on `:` here; the qualifier/value split is a
    // SECOND pass below (handles `project:"a b c"`).
    let tokStart = i;
    let buf = "";
    while (i < len && !/\s/.test(s[i])) {
      if (s[i] === '"') {
        // Quoted run — read until the closing quote (or EOL).
        i++;
        while (i < len && s[i] !== '"') {
          buf += s[i];
          i++;
        }
        if (i < len && s[i] === '"') i++;
      } else {
        buf += s[i];
        i++;
      }
    }
    // Now `buf` is the raw token text (with quotes stripped).
    // Look for a qualifier prefix `<key>:`.
    const colon = buf.indexOf(":");
    if (colon > 0 && /^[A-Za-z_][A-Za-z0-9_-]*$/.test(buf.slice(0, colon))) {
      const key = buf.slice(0, colon).toLowerCase();
      let value = buf.slice(colon + 1);
      // Tolerate `project: paper-scitex-clew` — if the value is empty
      // attach the NEXT whitespace-delimited token (which may itself be
      // a quoted run).
      if (value === "") {
        // Skip whitespace + read the next raw token (re-use the same
        // tokenizer inner loop for one iteration).
        let j = i;
        while (j < len && /\s/.test(s[j])) j++;
        if (j < len) {
          let vbuf = "";
          while (j < len && !/\s/.test(s[j])) {
            if (s[j] === '"') {
              j++;
              while (j < len && s[j] !== '"') {
                vbuf += s[j];
                j++;
              }
              if (j < len && s[j] === '"') j++;
            } else {
              vbuf += s[j];
              j++;
            }
          }
          value = vbuf;
          i = j;
        }
      }
      out.push({
        qualifier: key,
        value: value.trim(),
        raw: s.slice(tokStart, i).trim(),
      });
    } else {
      out.push({ text: buf });
    }
  }
  return out;
}

/* Parse a raw search string into { qualifiers, free, hints }.
 *   qualifiers: array of { name, canonical, value, strategy, enum?,
 *                          fields, unknown: bool, unknownValue: bool }
 *   free:       array of bare-token strings (lowercased, joined later)
 *   hints:      array of { label, value, unknown, unknownValue, suggestion? }
 *               — drives the hint-pill UI strip.
 */
function parseSearchQuery(input) {
  const tokens = tokenize(input);
  const qualifiers = [];
  const free = [];
  const hints = [];
  for (const tok of tokens) {
    if (tok.text != null) {
      if (tok.text.length > 0) free.push(tok.text);
      continue;
    }
    const name = tok.qualifier;
    const value = tok.value;
    if (QUALIFIERS.hasOwnProperty(name)) {
      const q = QUALIFIERS[name];
      let unknownValue = false;
      let suggestion = null;
      if (q.strategy === "enum") {
        const v = value.toLowerCase();
        if (v && !q.enum.includes(v)) {
          unknownValue = true;
          suggestion = q.enum.join(" / ");
        }
      }
      qualifiers.push({
        name,
        canonical: q.canonical,
        value,
        strategy: q.strategy,
        enum: q.enum,
        fields: q.fields,
        unknown: false,
        unknownValue,
      });
      hints.push({
        label: q.canonical,
        value,
        unknown: false,
        unknownValue,
        suggestion,
      });
    } else {
      // Unknown qualifier — show a grey hint pill, still recorded so the
      // matcher can filter to ZERO (so the operator sees "your typo
      // returned nothing" instead of being silently dropped).
      qualifiers.push({
        name,
        canonical: name,
        value,
        strategy: "unknown",
        fields: [],
        unknown: true,
        unknownValue: false,
      });
      hints.push({
        label: name,
        value,
        unknown: true,
        unknownValue: false,
        suggestion: didYouMean(name),
      });
    }
  }
  return {
    qualifiers,
    free,
    freeText: free.join(" "),
    hints,
    hasQualifiers: qualifiers.length > 0,
    raw: String(input == null ? "" : input),
  };
}

/* Build a "did you mean: project / agent / status / ..." suggestion. We
 * keep it simple — no Levenshtein — just emit the canonical list so the
 * operator sees the menu of options. */
function didYouMean(_name) {
  return KNOWN_QUALIFIER_NAMES.join(" / ");
}

/* === Matchers =============================================================
 * Each strategy is a pure fn: (task, qualifier) → bool. The qualifier
 * carries everything it needs (field list, enum, etc.) so the matcher
 * stays declarative. */
function _fieldValues(task, fields) {
  const out = [];
  for (const f of fields) {
    const v = task[f];
    if (v != null && v !== "") out.push(String(v));
  }
  return out;
}

function _substringMatch(task, qualifier) {
  const needle = String(qualifier.value || "")
    .toLowerCase()
    .trim();
  if (!needle) return true;
  const haystacks = _fieldValues(task, qualifier.fields);
  return haystacks.some((h) => h.toLowerCase().includes(needle));
}

function _exactMatch(task, qualifier) {
  const needle = String(qualifier.value || "").trim();
  if (!needle) return true;
  const haystacks = _fieldValues(task, qualifier.fields);
  return haystacks.some((h) => h === needle);
}

function _enumMatch(task, qualifier) {
  if (qualifier.unknownValue) return false;
  const needle = String(qualifier.value || "")
    .toLowerCase()
    .trim();
  if (!needle) return true;
  const haystacks = _fieldValues(task, qualifier.fields);
  return haystacks.some((h) => h.toLowerCase() === needle);
}

function _priorityMatch(task, qualifier) {
  const v = task.priority;
  if (typeof v !== "number") return false;
  const raw = String(qualifier.value || "").trim();
  if (!raw) return true;
  // Operators: <N, <=N, >N, >=N, =N (default if no op).
  let op = "=";
  let rest = raw;
  const m = raw.match(/^(<=|>=|<|>|=)(.*)$/);
  if (m) {
    op = m[1];
    rest = m[2];
  }
  const n = Number(rest);
  if (!Number.isFinite(n)) return false;
  switch (op) {
    case "<":
      return v < n;
    case "<=":
      return v <= n;
    case ">":
      return v > n;
    case ">=":
      return v >= n;
    case "=":
      return v === n;
    default:
      return false;
  }
}

const MATCHERS = {
  substring: _substringMatch,
  exact: _exactMatch,
  enum: _enumMatch,
  priority: _priorityMatch,
};

/* === Free-text (bare tokens) ==============================================
 * Re-uses the existing fzf-style subsequence behaviour from board_v3.html's
 * `fuzzyMatch` so PR #80's UX is preserved when no qualifier is present.
 * Whitespace-tolerant; case-insensitive. The free-text haystack covers
 * title + task + project + agent + note + id — same shape as the page's
 * existing `fuzzyMatch` so nothing regresses. */
function _freeTextMatch(task, freeText) {
  const q = String(freeText || "")
    .toLowerCase()
    .trim();
  if (!q) return true;
  const hay = (
    (task.title || "") +
    " " +
    (task.task || "") +
    " " +
    (task.project || "") +
    " " +
    (task.agent || "") +
    " " +
    (task.note || "") +
    " " +
    (task.id || "")
  ).toLowerCase();
  let i = 0;
  for (const c of q) {
    if (c === " ") continue;
    const found = hay.indexOf(c, i);
    if (found < 0) return false;
    i = found + 1;
  }
  return true;
}

/* === Top-level matcher ====================================================
 * Returns true when `task` passes EVERY qualifier (AND) AND the free-text
 * match. Unknown qualifiers (`status:hopefully-typo`) ALWAYS fail so the
 * operator sees the empty result + the grey hint pill explains why. */
function matchesSearchQuery(task, parsed) {
  if (!parsed) return true;
  for (const q of parsed.qualifiers) {
    if (q.unknown) return false;
    const matcher = MATCHERS[q.strategy];
    if (!matcher) return false;
    if (!matcher(task, q)) return false;
  }
  if (parsed.freeText && !_freeTextMatch(task, parsed.freeText)) return false;
  return true;
}

/* === ES module + CommonJS + globalThis export =============================
 * The board template loads this file as a plain <script>, so `window.STX`
 * (the existing global namespace, lazy-init'd) is the primary surface.
 * Module exports are kept for `node --test` unit-testability.
 *
 * NAME COLLISION (fixed 2026-07-13). This export object used to be called
 * `_api` — and so did searchSuggest.js's. A top-level `const` in a CLASSIC
 * (non-module) <script> lands in the SHARED global lexical scope, so the
 * SECOND file to load threw
 *
 *   Uncaught SyntaxError: Identifier '_api' has already been declared
 *
 * at instantiation time. The whole of searchSuggest.js therefore never ran
 * and `window.STX.searchSuggest` was undefined — i.e. SEARCH AUTOCOMPLETE
 * WAS SILENTLY DEAD on the live board. Each file now names its export after
 * itself, so the two can never clash again. */
const _searchQueryApi = {
  parseSearchQuery,
  matchesSearchQuery,
  tokenize,
  QUALIFIERS,
  KNOWN_QUALIFIER_NAMES,
  VALID_STATUSES,
  VALID_KINDS,
};

if (typeof globalThis !== "undefined") {
  globalThis.STX = globalThis.STX || {};
  globalThis.STX.searchQuery = _searchQueryApi;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = _searchQueryApi;
}
