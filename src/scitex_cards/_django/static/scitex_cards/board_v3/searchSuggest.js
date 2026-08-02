/* searchSuggest.js — Tab-completion / autocomplete for the GitHub-style
 * qualifier-syntax search bar (board_v3 + React mirror).
 *
 * Operator pain (TG 12318, lead a2a `e09e0c886eb94e509f8daa87c23dca2a`,
 * 2026-06-12): "want GitHub-style autocomplete on the qualifier search".
 * Extension of PR #102 (searchQuery.js) — same qualifier vocabulary,
 * same closed enums; this module adds the SUGGESTION layer (key
 * completion + value completion driven by the actual task-store data).
 *
 * Pure module — no DOM, no fetch, no zustand. The vanilla board_v3.html
 * + the React `SearchAutocomplete.tsx` both consume the same functions.
 * Unit tests at tests/scitex_cards/test__search_suggest.js (node --test).
 *
 * SUGGESTION SHAPE
 * ----------------
 *   tokenAtCursor(query, cursorPos) -> {
 *     prefix:        substring of query BEFORE the token under the cursor
 *     token:         the (possibly empty) token under the cursor
 *     suffix:        substring of query AFTER the token under the cursor
 *     kind:          "key" | "value"
 *     qualifierKey?: when kind === "value", the qualifier name preceding `:`
 *   }
 *
 *   keySuggestions(prefix) -> [{label, value, kind: "key", hint}]
 *   valueSuggestions(qualifierKey, prefix, dataSource) ->
 *       [{label, value, kind: "value", count?, hint?}]
 *
 *   applySuggestion(query, cursorPos, suggestion) -> {newQuery, newCursorPos}
 *
 *   formatSuggestion(suggestion) -> {label, hint}   (display helper)
 *
 * dataSource shape:
 *   { nodes: [{id, project?, repo?, agent?, assignee?, scope?, parent?,
 *              host?, status?, kind?, priority?}, …] }
 * — i.e. the same `graph.nodes` the board already loads. The module reads
 * ONLY the fields it needs; missing fields are ignored.
 *
 * DRY w/ searchQuery.js — we import its `KNOWN_QUALIFIER_NAMES` +
 * `QUALIFIERS` + `VALID_STATUSES` + `VALID_KINDS` rather than redefining
 * the qualifier list (operator's directive: "don't re-define"). When this
 * file loads as a browser <script>, it falls back to
 * `globalThis.STX.searchQuery` because the script loads after
 * searchQuery.js (deferred load order is preserved by the template).
 */
"use strict";

let _SQ;
try {
  /* eslint-disable @typescript-eslint/no-var-requires */
  _SQ = require("./searchQuery.js");
} catch (_e) {
  _SQ = null;
}
function _sq() {
  if (_SQ) return _SQ;
  if (
    typeof globalThis !== "undefined" &&
    globalThis.STX &&
    globalThis.STX.searchQuery
  ) {
    return globalThis.STX.searchQuery;
  }
  // Defensive empty fallback — the module degrades to no-op suggestions
  // rather than throwing if loaded in isolation (shouldn't happen in
  // production; covered by the JS test which require()s us directly).
  return {
    QUALIFIERS: {},
    KNOWN_QUALIFIER_NAMES: [],
    VALID_STATUSES: [],
    VALID_KINDS: [],
  };
}

/* === Hint strings for KEY suggestions =====================================
 * One-line description per qualifier, shown next to the `key:` label in the
 * dropdown. Sourced from the operator's brief + the searchQuery.js doc-
 * string. Kept short — the dropdown is narrow. */
const KEY_HINTS = {
  project: "qualify by project",
  repo: "alias of project:",
  agent: "qualify by agent",
  assignee: "alias of agent:",
  status: "qualify by status (enum)",
  kind: "qualify by kind (enum)",
  parent: "qualify by parent task id",
  scope: "qualify by scope",
  id: "qualify by task id (substring)",
  host: "qualify by compute host",
  priority: "qualify by priority (#, <N, >=N, …)",
};

/* === tokenAtCursor =======================================================
 * Find the token under the cursor — i.e. the run of non-whitespace chars
 * the cursor is in/at the end of — and classify it as a KEY or a VALUE.
 *
 *  - cursor at end of `pro|`        -> kind=key,  token="pro"
 *  - cursor at end of `project:pa|` -> kind=value, qualifierKey="project",
 *                                       token="pa"
 *  - cursor in middle of `project|:foo` -> kind=key, token="project"
 *  - cursor after a space `foo |`   -> kind=key, token=""
 *
 * Returns {prefix, token, suffix, kind, qualifierKey?}.
 *
 * The qualifierKey is taken VERBATIM (lowercased); the suggestion layer
 * only narrows when the key is known.
 */
function tokenAtCursor(query, cursorPos) {
  const q = String(query == null ? "" : query);
  const pos = Math.max(
    0,
    Math.min(q.length, Number.isFinite(cursorPos) ? cursorPos : q.length),
  );
  // Walk left to the start of the current whitespace-delimited token.
  let start = pos;
  while (start > 0 && !/\s/.test(q[start - 1])) start--;
  // Walk right to the end of the current whitespace-delimited token.
  let end = pos;
  while (end < q.length && !/\s/.test(q[end])) end++;
  const tokRun = q.slice(start, end);
  const prefix = q.slice(0, start);
  const suffix = q.slice(end);

  const relCursor = pos - start;
  const beforeCursor = tokRun.slice(0, relCursor);
  const afterCursor = tokRun.slice(relCursor);
  const colonIdx = beforeCursor.indexOf(":");

  if (colonIdx >= 0) {
    const key = beforeCursor.slice(0, colonIdx).toLowerCase();
    let valBefore = beforeCursor.slice(colonIdx + 1);
    let valAfter = afterCursor;
    if (valBefore.startsWith('"')) valBefore = valBefore.slice(1);
    if (valAfter.endsWith('"')) valAfter = valAfter.slice(0, -1);
    return {
      prefix,
      token: valBefore,
      tokenAfter: valAfter,
      suffix,
      kind: "value",
      qualifierKey: key,
    };
  }
  return {
    prefix,
    token: beforeCursor,
    tokenAfter: afterCursor,
    suffix,
    kind: "key",
  };
}

/* === keySuggestions ======================================================
 * Suggest qualifier KEYS that start with `prefix` (case-insensitive). The
 * canonical list comes from searchQuery.js (DRY w/ PR #102). Order: alpha
 * by key. Each suggestion's commit-value is `<key>:` (so the user
 * immediately can start typing the value).
 */
function keySuggestions(prefix) {
  const sq = _sq();
  const p = String(prefix == null ? "" : prefix).toLowerCase();
  const out = [];
  for (const key of sq.KNOWN_QUALIFIER_NAMES) {
    if (p === "" || key.startsWith(p)) {
      out.push({
        kind: "key",
        label: key + ":",
        value: key + ":",
        hint: KEY_HINTS[key] || "",
      });
    }
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

/* === Field harvesting ====================================================
 * Pull the unique values of a `nodes[].<field>` (with optional fallback
 * fields) and rank them by frequency. Returns
 *   [{value, count}, …]
 * sorted by count DESC then label ASC.
 */
function _harvestField(dataSource, fields) {
  const nodes = (dataSource && dataSource.nodes) || [];
  const counts = new Map();
  for (const n of nodes) {
    for (const f of fields) {
      const v = n[f];
      if (v == null || v === "") continue;
      const s = String(v);
      counts.set(s, (counts.get(s) || 0) + 1);
    }
  }
  const out = [];
  for (const [value, count] of counts) out.push({ value, count });
  out.sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  return out;
}

function _matchesPrefix(value, prefix) {
  if (!prefix) return true;
  return String(value).toLowerCase().includes(String(prefix).toLowerCase());
}

/* === valueSuggestions ====================================================
 * Suggest VALUES for `qualifierKey` that match `prefix`. Sources:
 *
 *   status      -> VALID_STATUSES (enum, alpha sort)
 *   kind        -> VALID_KINDS (enum, alpha sort)
 *   project     -> unique nodes[].project / repo, freq-desc sort
 *   repo        -> alias of project
 *   agent       -> unique nodes[].agent / assignee, freq-desc sort
 *   assignee    -> alias of agent
 *   scope       -> unique nodes[].scope, freq-desc sort
 *   parent      -> unique nodes[].parent, freq-desc sort
 *   host        -> unique nodes[].host, freq-desc sort
 *   id          -> all nodes[].id, alpha sort (1-per-task)
 *   priority    -> numeric — 1/2/3/4/5 + the operator hint set
 *                  (<N / <=N / >N / >=N)
 *
 * Returns [{kind:"value", label, value, count?, hint?}], capped at 8.
 *
 * Value is wrapped in `"…"` automatically when it contains whitespace,
 * since the searchQuery.js tokenizer recognizes quotes (PR #102).
 */
function valueSuggestions(qualifierKey, prefix, dataSource) {
  const sq = _sq();
  const key = String(qualifierKey || "").toLowerCase();
  const spec = sq.QUALIFIERS[key];
  if (!spec) return [];
  const p = String(prefix == null ? "" : prefix);

  let raw = [];
  if (spec.strategy === "enum") {
    for (const v of spec.enum || []) {
      raw.push({ value: v, count: undefined });
    }
    raw.sort((a, b) => a.value.localeCompare(b.value));
  } else if (key === "priority") {
    const seen = _harvestField(dataSource, ["priority"]);
    const seenSet = new Set(seen.map((e) => String(e.value)));
    const out = [];
    for (const e of seen) out.push({ value: String(e.value), count: e.count });
    for (const n of ["1", "2", "3", "4", "5"]) {
      if (!seenSet.has(n)) out.push({ value: n, count: undefined });
    }
    for (const op of ["<2", "<=2", ">2", ">=2"]) {
      out.push({ value: op, count: undefined, hint: "range" });
    }
    raw = out;
  } else {
    raw = _harvestField(dataSource, spec.fields);
  }

  const filtered = raw.filter((e) => _matchesPrefix(e.value, p));

  const out = filtered.slice(0, 8).map((e) => {
    const needsQuote = /\s/.test(e.value);
    const commitValue = needsQuote ? `"${e.value}"` : e.value;
    const hint =
      e.hint != null
        ? e.hint
        : e.count != null
          ? `${e.count} task${e.count === 1 ? "" : "s"}`
          : "";
    return {
      kind: "value",
      label: e.value,
      value: commitValue,
      count: e.count,
      hint,
    };
  });
  return out;
}

/* === applySuggestion =====================================================
 * Given the current query + cursor + the chosen suggestion, return the
 * new query + new cursor position. The cursor lands AT THE END of the
 * inserted text so the user can keep typing immediately (e.g. select
 * `project:` -> cursor sits right after the colon ready for the value).
 *
 *   query     = "pro"        cursor=3   suggestion={kind:"key", value:"project:"}
 *   -> newQuery="project:"   newCursorPos=8
 *
 *   query     = "project:pa" cursor=10  suggestion={kind:"value", value:"paper-scitex-clew"}
 *   -> newQuery="project:paper-scitex-clew" newCursorPos=25
 *
 *   query     = "foo project:pa baz"   cursor=14
 *   -> the suggestion replaces only the `pa` -> "foo project:paper-scitex-clew baz"
 */
function applySuggestion(query, cursorPos, suggestion) {
  const tok = tokenAtCursor(query, cursorPos);
  const sug = suggestion || {};
  let newQuery;
  let newCursorPos;
  if (sug.kind === "key") {
    let tail = tok.tokenAfter || "";
    if (tail.startsWith(":")) tail = tail.slice(1);
    newQuery = tok.prefix + sug.value + tail + tok.suffix;
    newCursorPos = (tok.prefix + sug.value).length;
  } else if (sug.kind === "value") {
    const keyPrefix = tok.qualifierKey ? tok.qualifierKey + ":" : "";
    let tail = tok.tokenAfter || "";
    if (tail.startsWith('"')) tail = tail.slice(1);
    newQuery = tok.prefix + keyPrefix + sug.value + tail + tok.suffix;
    newCursorPos = (tok.prefix + keyPrefix + sug.value).length;
  } else {
    newQuery = String(query == null ? "" : query);
    newCursorPos = Number.isFinite(cursorPos) ? cursorPos : newQuery.length;
  }
  return { newQuery, newCursorPos };
}

/* === formatSuggestion ====================================================
 * Cheap display helper for the dropdown row. Returns {label, hint}.
 * The dropdown renders the label (bold) + the hint (faint) on the right.
 */
function formatSuggestion(suggestion) {
  if (!suggestion) return { label: "", hint: "" };
  return {
    label: suggestion.label,
    hint: suggestion.hint || "",
  };
}

/* === computeSuggestions ==================================================
 * Convenience top-level entry: given the raw query + cursor + dataSource,
 * return the suggestion list (key or value) for the token under the
 * cursor. Used by the UI to call ONE function per keystroke.
 */
function computeSuggestions(query, cursorPos, dataSource) {
  const tok = tokenAtCursor(query, cursorPos);
  if (tok.kind === "key") {
    return keySuggestions(tok.token);
  }
  return valueSuggestions(tok.qualifierKey, tok.token, dataSource);
}

/* Named after this file, NOT `_api` — see the long note in searchQuery.js.
 * Both files used to declare a top-level `const _api`, which in classic
 * <script> scope is one shared global lexical binding: the second script to
 * load threw "Identifier '_api' has already been declared" and never ran, so
 * search autocomplete was dead on the live board. (Fixed 2026-07-13.) */
const _searchSuggestApi = {
  tokenAtCursor,
  keySuggestions,
  valueSuggestions,
  applySuggestion,
  formatSuggestion,
  computeSuggestions,
  KEY_HINTS,
};

if (typeof globalThis !== "undefined") {
  globalThis.STX = globalThis.STX || {};
  globalThis.STX.searchSuggest = _searchSuggestApi;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = _searchSuggestApi;
}
