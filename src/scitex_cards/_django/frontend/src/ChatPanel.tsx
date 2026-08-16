/** Operator↔agent CHAT panel — fleet Phase-6 surface.
 *
 * Lead a2a `74db4f2d` + `10afa799` greenlight (TRACK-2 last surface,
 * 2026-06-14). The substrate (per-card `comments[]` list + the existing
 * comment-section in NodeDetailPanel) is already there; this panel
 * adds the WRITE-BACK UI for the operator and a 30s auto-poll so the
 * operator's "live conversation" intent works end-to-end.
 *
 * Wire shape (matches `handlers/chat.py`):
 *
 *   GET  /chat/<card_id> -> { card_id, title, comments: [{ts, author, text}, ...] }
 *   POST /chat/<card_id> body = { text, author? }
 *
 * Design principles (HARD, from the operator brief):
 *
 *   - fail-loud: a write failure surfaces a toast via the board store
 *     (`showToast`); we DO NOT silently lose the message. The textarea
 *     keeps the text so the operator can retry.
 *   - registry-sourced: comments flow from the same per-card storage the
 *     existing NodeDetailPanel comment section reads from — no parallel
 *     state.
 *   - NO hardcoded proper nouns: the author field defaults to the
 *     `SCITEX_CARDS_AGENT` value read from the page-load env (rendered
 *     into the bundle by the Django template) with operator-typed
 *     override. The hash-to-color helper is a stable, content-agnostic
 *     mapping from a string to a scitex-ui token.
 *
 * Out of scope (deferred, flagged with CARDs):
 *   - RW-perm gating (operator-write, agents-read).
 *   - WebSocket push (polling at 30s is fine for the floor).
 *   - Markdown rendering, @-mentions, threading, reactions, attachments.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** localStorage key remembering the last author typed, so the operator
 *  doesn't re-enter their name each time. Shared shape with the legacy
 *  comment section so both surfaces persist a single value. */
const AUTHOR_KEY = "stx-cards-comment-author";

/** Polling cadence — same 30s as the other fleet surfaces (CI pills,
 *  timing, timeline). Keeps the operator's "what just changed"
 *  cognitive load uniform. */
export const CHAT_POLL_INTERVAL_MS = 30_000;

/** Closed palette of scitex-ui accent tokens the author-color hash maps
 *  into. Six entries balances "distinct enough to track speakers" with
 *  "small enough that two authors rarely re-roll the same slot". No raw
 *  hex / rgb here — the tokens flip with the light/dark theme via
 *  board.css. */
export const AUTHOR_COLOR_TOKENS: readonly string[] = [
  "var(--stx-accent)",
  "var(--stx-text)",
  "var(--stx-text-muted)",
  "var(--stx-border-strong)",
  "var(--stx-accent-on)",
  "var(--stx-text-faint)",
];

/** Map an author string to a stable scitex-ui token color.
 *
 *  The hash is a tiny djb2 variant — content-agnostic, deterministic,
 *  zero-deps. `null` / empty input lands in the "unknown" slot (the
 *  muted-text token) so unlabeled comments still render legibly.
 *
 *  Exported so the predicate tests can pin the contract directly. */
export function authorColorToken(author: string | null | undefined): string {
  const s = (author ?? "").trim();
  if (s === "") return AUTHOR_COLOR_TOKENS[2]; // muted slot
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) {
    h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  }
  return AUTHOR_COLOR_TOKENS[h % AUTHOR_COLOR_TOKENS.length];
}

/** Append a new comment to the existing list. Pure, order-preserving.
 *  Exported so the predicate test can pin the contract — keeps the
 *  React component's state-reducer logic test-pinned without dragging
 *  a DOM into the test. */
export function appendComment(
  current: ChatComment[],
  next: ChatComment,
): ChatComment[] {
  return [...current, next];
}

export interface ChatComment {
  ts: string;
  author: string;
  text: string;
}

interface ChatPayload {
  card_id: string;
  title: string;
  comments: ChatComment[];
}

/** Format a UTC ISO timestamp for the bubble metadata row. Falls back
 *  to the raw string on a parse failure so we never crash the panel on
 *  a malformed comment row. */
function fmtTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Read the `SCITEX_CARDS_AGENT` value rendered into the page by the
 *  Django template. Falls through to a persisted localStorage value
 *  (so operator-typed overrides survive a reload) and finally an empty
 *  string (which the backend then resolves to `<unknown>`). No hardcoded
 *  agent names — the env is the source of truth. */
function defaultAuthor(): string {
  const w = window as unknown as { SCITEX_CARDS_AGENT?: string };
  const fromEnv = (w.SCITEX_CARDS_AGENT ?? "").trim();
  if (fromEnv) return fromEnv;
  return localStorage.getItem(AUTHOR_KEY) ?? "";
}

/** The chat panel — drops into NodeDetailPanel's drawer alongside the
 *  existing comment section. The two render the same per-card
 *  `comments[]` substrate; the chat surface adds:
 *   - 30s auto-poll for new comments (the FE's "live conversation"
 *     intent floor).
 *   - Optimistic-append on send + toast-on-failure (fail-loud).
 *   - Author-color hash so multi-party threads stay readable at a glance.
 *
 *  Errors flow into a local `error` state and the textarea retains the
 *  draft on failure so the operator can retry without re-typing. */
export function ChatPanel({
  cardId,
  showToast,
}: {
  cardId: string;
  showToast?: (msg: string) => void;
}): JSX.Element {
  const [comments, setComments] = useState<ChatComment[]>([]);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState<string>(() => defaultAuthor());
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const mountedRef = useRef(true);

  const endpoint = useMemo(
    () => `/chat/${encodeURIComponent(cardId)}`,
    [cardId],
  );

  const fetchComments = useCallback(async (): Promise<void> => {
    try {
      const res = await fetch(endpoint, { method: "GET" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(body.error || `chat GET ${res.status}`);
      }
      const payload = (await res.json()) as ChatPayload;
      if (!mountedRef.current) return;
      setComments(payload.comments ?? []);
      setError(null);
    } catch (e) {
      if (!mountedRef.current) return;
      setError((e as Error).message);
    }
  }, [endpoint]);

  // Initial load + 30s poll while the panel is open. The interval
  // tears down on unmount (drawer close), so polling stops the moment
  // the operator dismisses the card.
  useEffect(() => {
    mountedRef.current = true;
    void fetchComments();
    const id = window.setInterval(() => {
      void fetchComments();
    }, CHAT_POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      window.clearInterval(id);
    };
  }, [fetchComments]);

  const onSend = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const body = text.trim();
      if (!body || sending) return;
      const a = author.trim();
      if (a) localStorage.setItem(AUTHOR_KEY, a);
      setSending(true);
      setError(null);
      try {
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: body, author: a || undefined }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: res.statusText }));
          throw new Error(err.error || `chat POST ${res.status}`);
        }
        const payload = (await res.json()) as { comment: ChatComment };
        if (mountedRef.current) {
          setComments((cur) => appendComment(cur, payload.comment));
          setText("");
        }
      } catch (err) {
        const msg = (err as Error).message;
        if (mountedRef.current) {
          setError(msg);
        }
        if (showToast) showToast(`Chat send failed: ${msg}`);
      } finally {
        if (mountedRef.current) setSending(false);
      }
    },
    [author, endpoint, sending, showToast, text],
  );

  return (
    <section className="stx-cards-chat" aria-label="Chat thread">
      <h3 className="stx-cards-chat__title">
        Chat {comments.length > 0 && `(${comments.length})`}
      </h3>
      {comments.length === 0 ? (
        <p className="stx-cards-chat__empty">
          <em>No messages yet — start the conversation below.</em>
        </p>
      ) : (
        <ul className="stx-cards-chat__list">
          {comments.map((c, i) => (
            <li
              className="stx-cards-chat__bubble"
              key={`${c.ts}-${i}`}
              data-author={c.author}
            >
              <div className="stx-cards-chat__meta">
                <span
                  className="stx-cards-chat__author"
                  style={{ color: authorColorToken(c.author) }}
                >
                  {c.author || "<unknown>"}
                </span>
                <span className="stx-cards-chat__ts">{fmtTs(c.ts)}</span>
              </div>
              <div className="stx-cards-chat__text">{c.text}</div>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="stx-cards-chat__error" role="alert">
          {error}
        </p>
      )}
      <form className="stx-cards-chat__form" onSubmit={onSend}>
        <input
          className="stx-cards-input stx-cards-chat__author-input"
          value={author}
          placeholder="your name (defaults to SCITEX_CARDS_AGENT)"
          onChange={(e) => setAuthor(e.target.value)}
          aria-label="Chat author"
        />
        <textarea
          className="stx-cards-input stx-cards-chat__text-input"
          value={text}
          placeholder="Type a message and press Send…"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          aria-label="Chat message text"
        />
        <div className="stx-cards-chat__actions">
          <button
            type="submit"
            className="stx-cards-btn stx-cards-btn--primary"
            disabled={sending || !text.trim()}
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
      {/* CARD(phase-7): RW-perm gating — operator-write, agents-read.
          CARD(phase-7): swap polling for a WebSocket push channel. */}
    </section>
  );
}
