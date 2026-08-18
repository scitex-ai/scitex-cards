---
description: |
  [TOPIC] The Stop hook as a SECOND delivery rail
  [DETAILS] `scitex-cards stop-hook` does two jobs: it refuses a stop while the
  agent's board holds runnable work, AND it delivers pending notifications
  itself, requiring an acknowledgement before the turn may end — so delivery
  never depends on the push rail alone.
tags: [scitex-cards-stop-hook-second-delivery-rail]
---

# The Stop hook as a SECOND delivery rail

`scitex-cards stop-hook` now does two jobs. It still refuses a stop while the
agent's board holds runnable work. It also **delivers the agent's pending
notifications itself** and requires an acknowledgement before the turn may end.

## Why a second rail exists

Delivery had exactly **one** rail: the MCP channel push. An agent spec
whitelisted `server:scitex-cards` while `.mcp.json` registered the server as
`scitex-cards` (renamed during the migration), so Claude Code **silently
discarded every push**. `send()` returned normally, the drain acked on that
success, and the message was gone. Measured on the affected agent: **228 inbox
rows, ZERO unseen**, roughly three weeks of operator DMs. sac later found the
same hazard armed on ~96 spec entries fleet-wide.

Fixing that one spec is not a fix for the class. A single rail with nothing
independent checking it fails again, for some other reason, and it fails
**silently** — because *the transport returned* is not *the recipient received*.

This rail reads the store directly at turn end. It does not use the push and
cannot be silenced by a channel-registration mistake.

## The order of operations IS the safety property

```
1. PULL     poll the pending notifications — a pure read, cursor untouched
2. PRESENT  put their text in front of the agent (the hook's `reason` IS the delivery)
3. REQUIRE  only now demand the ack before the turn may finish
4. ACK      ack_notifications(agent, ids) — per id, idempotent
```

A hook that merely blocked on unacked messages would have **deadlocked every
agent** on the morning of the outage: nothing had been shown, so nothing could
have been acked. Blocking an actor where the actor cannot remediate is
forbidden. The agent can always comply here, because the hook itself just
delivered the thing it is asking about.

That is enforced structurally, not by care:
`scitex_cards._inbox_present.present()` returns `(text, presented_ids)` and
`presented_ids` holds **exactly** the ids whose content is in `text`. The hook
demands acks for those ids and for no others. A message that did not fit the
turn's budget is counted out loud and left unconfirmed — so the next poll
returns it and the next turn presents it.

## How to comply

```
ack_notifications(agent="<you>", ids=["n_...", "n_..."])     # MCP
scitex-cards inbox ack --agent <you> n_... n_...             # no MCP needed
```

Both reach the same single verb (`_inbox_confirm.confirm_notifications`).
There is no second ack path. Confirming is idempotent; anything you do not
confirm stays unseen and comes back.

## The bound — what happens when things go wrong

A hook that can refuse forever is a new outage, so every failure has a stated
answer:

| Failure | Behaviour |
| --- | --- |
| The ack itself fails | Nothing is lost. Unconfirmed means unseen means redelivered next turn. |
| The store is unreadable or absent | **Allow the stop**, explain on stderr. Detection failing must never wedge an agent. |
| No agent id resolvable | **Allow the stop**, explain on stderr. |
| Same message unacked N times (N=3, per session) | Stop *demanding* it, warn naming the id. The record stays unseen in the store — we stop escalating, we do not stop remembering. |
| The retry counter cannot be persisted | Fall back to the harness's `stop_hook_active`: already-continuing plus cannot-count means allow. Degrading to block-once is the safe direction. |
| The reason would be empty | Never block. A refusal that says nothing leaves the agent stopped-but-refused, which is still idle. |

Each rail fails open **independently**: a broken board still lets a pending
message through, and a broken inbox still lets runnable cards through.

## Registration — the capability nobody wires protects nobody

```bash
scitex-cards install-stop-hook            # dry run, prints what it would change
scitex-cards install-stop-hook --apply    # writes ~/.claude/settings.json, backs up first
```

Measured 2026-07-29 on the agent that suffered the outage: its `Stop` group held
four commands and `scitex-cards stop-hook` was **not** among them. The mechanism
worked when invoked by hand and protected nobody. Check before assuming:

```bash
python -c "import json,pathlib;print(json.loads(pathlib.Path.home().joinpath('.claude/settings.json').read_text())['hooks']['Stop'])"
```

## Cards owns the hook. There is no leaf-plugin mechanism to declare it to.

Surveyed 2026-07-29, and stated plainly because a convention invented in silence
is worse than a named open question:

* **scitex-dev's hook aggregator does not accept leaf declarations.** It is a
  hardcoded dict of scitex-dev's own scripts
  (`scitex_dev/_cli/_hooks_cli/_registry.py::KNOWN_HOOKS`), all git/PostToolUse
  hooks, none of them a `Stop` hook. Its own comment says the extension model is
  "a single dict entry here plus a one-line accessor" — i.e. **edit scitex-dev**,
  not declare from a leaf. Neither `_install.py` nor `_inspect.py` reads
  `entry_points`.
* **`scitex_dev.linter.plugins` is not it** — that is a real entry-point group,
  for lint rules.
* **`scitex_cards.hooks` is not it either** — that is *our own* in-process
  card-event bus (`dispatch_event`), unrelated to Claude Code lifecycle hooks.
  The name collision is a genuine trap.
* **Claude Code's native plugin system** (`.claude-plugin/`, `hooks/hooks.json`,
  `${CLAUDE_PLUGIN_ROOT}`) is supported by the harness, but no package in this
  ecosystem ships one.

So cards owns both ends: it emits the hook (`scitex-cards stop-hook`) and it
registers the hook (`scitex-cards install-stop-hook --apply`). That is the
operator's 「リーフがその守備範囲を自分でカバーする」 with the tools that
actually exist today.

### The ONE integration point, if a fleet-wide rail is later wanted

Exactly one line in scitex-dev, and nothing in cards changes:

```python
# scitex_dev/_cli/_hooks_cli/_registry.py
KNOWN_HOOKS["cards_stop"] = ("scitex-cards stop-hook", ".claude/settings.json#Stop")
```

That inverts the dependency (scitex-dev would then know about cards), which is
why it is **not** done here. The alternative — cards shipping a real
`.claude-plugin/` with `hooks/hooks.json` — keeps the dependency direction
correct and is the better long-term answer; it is left open deliberately rather
than guessed at.

**Standing constraint:** scitex-cards must not depend on
scitex-agent-container or claude-code-telegrammer. This rail imports neither and
assumes no sac-managed environment. It works for an agent that installed
scitex-cards and nothing else.


