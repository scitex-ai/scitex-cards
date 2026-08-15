/* test__me_view.js — node --test unit tests for the "My Cards" phone page's
 * pure view decisions, in
 * ``src/scitex_cards/_django/static/scitex_cards/me/me_view.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__me_view.js
 *
 * Requires the REAL served module — not a hand-ported mirror, which drifts
 * from the file the browser loads and then lets both "pass" while
 * disagreeing. The module is DOM-free precisely so this require() works:
 * that, not the separate filename, is what makes it testable.
 *
 * Time is passed IN to every time-dependent function rather than read from
 * the clock, so these tests state an instant instead of racing one.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const MOD = require(
  path.resolve(
    __dirname,
    "..",
    "..",
    "src",
    "scitex_cards",
    "_django",
    "static",
    "scitex_cards",
    "me",
    "me_view.js",
  ),
);

const { deadlineState, planSections, refusalMessage, relativeTime, summarise } =
  MOD;

const NOW = Date.parse("2026-08-14T12:00:00Z");

/* ── planSections: grouping, order, and what is left out ───────────────── */

test("sections come back in the page's own order, not the input's", () => {
  const cards = [
    { id: "c", status: "deferred" },
    { id: "a", status: "in_progress" },
    { id: "b", status: "blocked" },
  ];
  const keys = planSections(cards).map((s) => s.key);
  assert.deepEqual(keys, ["in_progress", "blocked", "deferred"]);
});

test("a section carries every card of its status", () => {
  const cards = [
    { id: "a", status: "in_progress" },
    { id: "b", status: "in_progress" },
  ];
  const [section] = planSections(cards);
  assert.deepEqual(
    section.cards.map((c) => c.id),
    ["a", "b"],
  );
});

test("card order WITHIN a section is preserved from the server", () => {
  /* The endpoint already sorted by priority then recency. Re-sorting here
   * would make the phone disagree with the API for no stated reason. */
  const cards = [
    { id: "first", status: "in_progress" },
    { id: "second", status: "in_progress" },
  ];
  const [section] = planSections(cards);
  assert.equal(section.cards[0].id, "first");
});

test("empty sections are dropped rather than drawn as bare headings", () => {
  const keys = planSections([{ id: "a", status: "in_progress" }]).map(
    (s) => s.key,
  );
  assert.deepEqual(keys, ["in_progress"]);
});

test("no cards means no sections", () => {
  assert.deepEqual(planSections([]), []);
});

test("a missing card list is treated as empty, not thrown on", () => {
  /* The list is absent on every refusal body, and a page that throws while
   * rendering "you have no cards" shows a blank screen instead. */
  assert.deepEqual(planSections(undefined), []);
});

test("an unknown status still gets a section so the card stays visible", () => {
  const sections = planSections([{ id: "a", status: "invented-later" }]);
  assert.equal(sections.length, 1);
});

test("an unknown status sorts after every known one", () => {
  const cards = [
    { id: "a", status: "invented-later" },
    { id: "b", status: "in_progress" },
  ];
  const keys = planSections(cards).map((s) => s.key);
  assert.deepEqual(keys, ["in_progress", "invented-later"]);
});

test("a card with no status at all is still rendered", () => {
  const sections = planSections([{ id: "a" }]);
  assert.equal(sections[0].cards.length, 1);
});

/* ── relativeTime: coarse by design ───────────────────────────────────── */

test("minutes read as minutes", () => {
  assert.equal(relativeTime("2026-08-14T11:30:00Z", NOW), "30m ago");
});

test("hours read as hours", () => {
  assert.equal(relativeTime("2026-08-14T09:00:00Z", NOW), "3h ago");
});

test("days read as days", () => {
  assert.equal(relativeTime("2026-08-12T12:00:00Z", NOW), "2d ago");
});

test("months read as months", () => {
  assert.equal(relativeTime("2026-06-14T12:00:00Z", NOW), "2mo ago");
});

test("anything under a minute reads as just now", () => {
  assert.equal(relativeTime("2026-08-14T11:59:30Z", NOW), "just now");
});

test("a future stamp reads as just now rather than a negative age", () => {
  /* Clock skew between the server and a phone is normal; "-3m ago" is not. */
  assert.equal(relativeTime("2026-08-14T12:30:00Z", NOW), "just now");
});

test("an absent stamp renders nothing at all", () => {
  assert.equal(relativeTime(undefined, NOW), "");
});

test("an unparseable stamp renders nothing rather than NaN", () => {
  assert.equal(relativeTime("not a date", NOW), "");
});

/* ── deadlineState: the page notices, because nothing else does ────────── */

test("a past deadline is overdue", () => {
  assert.equal(deadlineState({ deadline: "2026-08-13" }, NOW), "overdue");
});

test("a deadline within the day reads as today", () => {
  assert.equal(
    deadlineState({ deadline: "2026-08-14T20:00:00Z" }, NOW),
    "today",
  );
});

test("a deadline a couple of days out reads as soon", () => {
  assert.equal(deadlineState({ deadline: "2026-08-16" }, NOW), "soon");
});

test("a distant deadline reads as later", () => {
  assert.equal(deadlineState({ deadline: "2026-12-25" }, NOW), "later");
});

test("no deadline reads as nothing", () => {
  assert.equal(deadlineState({ id: "a" }, NOW), "");
});

test("the server-computed next occurrence wins over the raw deadline", () => {
  /* deadline_next is what the server expands recurring/multi deadlines into;
   * reading the raw field first would mark a live recurring card overdue. */
  const card = { deadline: "2026-08-01", deadline_next: "2026-12-25" };
  assert.equal(deadlineState(card, NOW), "later");
});

/* ── refusalMessage: two different problems, two different answers ─────── */

test("an unlinked account is told its account is not linked", () => {
  const message = refusalMessage({ reason: "unlinked-email" });
  assert.match(message.title, /not linked/i);
});

test("an unlinked account keeps the address that needs linking", () => {
  const message = refusalMessage({
    reason: "unlinked-email",
    email: "someone@example.com",
  });
  assert.equal(message.email, "someone@example.com");
});

test("an anonymous visitor is NOT told to link an account", () => {
  /* The screen that would do the linking does not exist on a board with no
   * per-user login, so sending them to look for it is a dead end. */
  const message = refusalMessage({ reason: "anonymous" });
  assert.doesNotMatch(message.title, /not linked/i);
});

test("the server's own explanation is preferred when it sends one", () => {
  const message = refusalMessage({
    reason: "anonymous",
    detail: "Server says",
  });
  assert.equal(message.detail, "Server says");
});

test("a refusal with no body still explains something", () => {
  const message = refusalMessage(undefined);
  assert.ok(message.detail.length > 0);
});

/* ── summarise: agrees with the list it sits above ─────────────────────── */

test("the summary counts from the payload rather than recounting", () => {
  const payload = { total: 3, counts: { in_progress: 2, blocked: 1 } };
  assert.match(summarise(payload), /^3 cards/);
});

test("one card is not pluralised", () => {
  assert.match(summarise({ total: 1, counts: { in_progress: 1 } }), /^1 card/);
});

test("blocked work is called out in the summary", () => {
  const payload = { total: 3, counts: { in_progress: 2, blocked: 1 } };
  assert.match(summarise(payload), /1 blocked/);
});

test("an empty plate says so in words", () => {
  assert.equal(summarise({ total: 0, counts: {} }), "Nothing on your plate");
});

test("a missing payload does not throw while rendering the empty state", () => {
  assert.equal(summarise(undefined), "Nothing on your plate");
});

/* EOF */
