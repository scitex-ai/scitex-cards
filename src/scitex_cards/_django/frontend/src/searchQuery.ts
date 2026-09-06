/* searchQuery.ts — GitHub-style qualifier-syntax parser for the board
 * search input (React/Vite-side mirror of the vanilla-JS module shipped
 * at static/scitex_cards/board_v3/searchQuery.js).
 *
 * Operator pain (TG 12315 / 12316, lead a2a 7dde227a, 2026-06-12): the
 * operator typed `project: paper-scitex-clew` into the board search bar
 * — i.e. they already expected GitHub-style `<key>:<value>` qualifier
 * syntax — and was surprised it fell through to the plain fuzzy match.
 *
 * The board_v3 (live-operator) template loads the .js sibling directly.
 * This .ts file is the TYPED equivalent used by the React CardsBoard
 * (frontend/src/) so both code paths use the same parser semantics. The
 * implementations are kept in lock-step manually for now (the JS file
 * is the canonical source; this TS file mirrors the same dictionary
 * + matchers). Long-term option: extract to a shared package or
 * generate one from the other; YAGNI for now.
 *
 * Pure module — no React, no DOM, no zustand. Unit tests live alongside
 * the JS sibling at tests/scitex_cards/test__search_query.js (node --test).
 */

export interface Task {
  id?: string | null;
  title?: string | null;
  status?: string | null;
  priority?: number | null;
  note?: string | null;
  repo?: string | null;
  parent?: string | null;
  project?: string | null;
  agent?: string | null;
  assignee?: string | null;
  scope?: string | null;
  kind?: string | null;
  host?: string | null;
  task?: string | null;
}

export const VALID_STATUSES = [
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
] as const;

export const VALID_KINDS = ["task", "compute", "decision"] as const;

type Strategy = "substring" | "exact" | "enum" | "priority" | "unknown";

interface QualifierSpec {
  canonical: string;
  strategy: Strategy;
  fields: (keyof Task)[];
  enum?: readonly string[];
}

export const QUALIFIERS: Record<string, QualifierSpec> = {
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

export const KNOWN_QUALIFIER_NAMES = Object.keys(QUALIFIERS);

export interface ParsedQualifier {
  name: string;
  canonical: string;
  value: string;
  strategy: Strategy;
  fields: (keyof Task)[];
  unknown: boolean;
  unknownValue: boolean;
}

export interface QualifierHint {
  label: string;
  value: string;
  unknown: boolean;
  unknownValue: boolean;
  suggestion: string | null;
}

export interface ParsedQuery {
  qualifiers: ParsedQualifier[];
  free: string[];
  freeText: string;
  hints: QualifierHint[];
  hasQualifiers: boolean;
  raw: string;
}

interface RawToken {
  qualifier?: string;
  value?: string;
  text?: string;
}

/** Tokenize a raw query string (see searchQuery.js for the spec). */
export function tokenize(input: string | null | undefined): RawToken[] {
  const out: RawToken[] = [];
  const s = String(input == null ? "" : input);
  let i = 0;
  const len = s.length;
  while (i < len) {
    while (i < len && /\s/.test(s[i])) i++;
    if (i >= len) break;
    let buf = "";
    while (i < len && !/\s/.test(s[i])) {
      if (s[i] === '"') {
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
    const colon = buf.indexOf(":");
    if (colon > 0 && /^[A-Za-z_][A-Za-z0-9_-]*$/.test(buf.slice(0, colon))) {
      const key = buf.slice(0, colon).toLowerCase();
      let value = buf.slice(colon + 1);
      if (value === "") {
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
      out.push({ qualifier: key, value: value.trim() });
    } else {
      out.push({ text: buf });
    }
  }
  return out;
}

function didYouMean(): string {
  return KNOWN_QUALIFIER_NAMES.join(" / ");
}

export function parseSearchQuery(
  input: string | null | undefined,
): ParsedQuery {
  const tokens = tokenize(input);
  const qualifiers: ParsedQualifier[] = [];
  const free: string[] = [];
  const hints: QualifierHint[] = [];
  for (const tok of tokens) {
    if (tok.text != null) {
      if (tok.text.length > 0) free.push(tok.text);
      continue;
    }
    const name = tok.qualifier!;
    const value = tok.value ?? "";
    if (Object.prototype.hasOwnProperty.call(QUALIFIERS, name)) {
      const q = QUALIFIERS[name];
      let unknownValue = false;
      let suggestion: string | null = null;
      if (q.strategy === "enum") {
        const v = value.toLowerCase();
        if (v && q.enum && !q.enum.includes(v)) {
          unknownValue = true;
          suggestion = q.enum.join(" / ");
        }
      }
      qualifiers.push({
        name,
        canonical: q.canonical,
        value,
        strategy: q.strategy,
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
        suggestion: didYouMean(),
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

function fieldValues(task: Task, fields: (keyof Task)[]): string[] {
  const out: string[] = [];
  for (const f of fields) {
    const v = task[f];
    if (v != null && v !== "") out.push(String(v));
  }
  return out;
}

function substringMatch(task: Task, q: ParsedQualifier): boolean {
  const needle = String(q.value || "")
    .toLowerCase()
    .trim();
  if (!needle) return true;
  return fieldValues(task, q.fields).some((h) =>
    h.toLowerCase().includes(needle),
  );
}

function exactMatch(task: Task, q: ParsedQualifier): boolean {
  const needle = String(q.value || "").trim();
  if (!needle) return true;
  return fieldValues(task, q.fields).some((h) => h === needle);
}

function enumMatch(task: Task, q: ParsedQualifier): boolean {
  if (q.unknownValue) return false;
  const needle = String(q.value || "")
    .toLowerCase()
    .trim();
  if (!needle) return true;
  return fieldValues(task, q.fields).some((h) => h.toLowerCase() === needle);
}

function priorityMatch(task: Task, q: ParsedQualifier): boolean {
  const v = task.priority;
  if (typeof v !== "number") return false;
  const raw = String(q.value || "").trim();
  if (!raw) return true;
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

function freeTextMatch(task: Task, freeText: string): boolean {
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

export function matchesSearchQuery(
  task: Task,
  parsed: ParsedQuery | null | undefined,
): boolean {
  if (!parsed) return true;
  for (const q of parsed.qualifiers) {
    if (q.unknown) return false;
    let ok = false;
    switch (q.strategy) {
      case "substring":
        ok = substringMatch(task, q);
        break;
      case "exact":
        ok = exactMatch(task, q);
        break;
      case "enum":
        ok = enumMatch(task, q);
        break;
      case "priority":
        ok = priorityMatch(task, q);
        break;
      default:
        return false;
    }
    if (!ok) return false;
  }
  if (parsed.freeText && !freeTextMatch(task, parsed.freeText)) return false;
  return true;
}
