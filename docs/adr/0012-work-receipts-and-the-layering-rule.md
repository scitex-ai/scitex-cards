# ADR-0012 — Work receipts: transport success is not work success

**Status:** PROPOSED (operator-directed 2026-07-18; co-designed with
scitex-agent-container, who co-signed the ternary and the 202 shape)
**Owner:** scitex-cards (contract + record); the ACTOR side is owned by whoever
executes work — today that is scitex-agent-container
**Cards:** `incident-fleet-liveness-agents-stop-silently-20260718`,
`may-stop-hook-cards-runnable-work-20260718`

## Context — an incident, and eight failures with one shape

An agent (`scitex-hub`) sat idle for 80+ minutes holding five `in_progress`
cards. The OPERATOR noticed, twice, and had to supply the cause himself
("Login expired — it needs a restart"). Every automated signal we had said
things were fine.

The re-drive made it worse in an instructive way: `agent_send` returned
**HTTP 200 with an empty body**, and `idle_seconds` had not moved 75 seconds
later. Both facts were recorded. The conclusion drawn was "dispatched" —
reported as if it were "recovered".

Reviewing the night, eight distinct failures across four subsystems share one
shape: **a check that could not tell was read as a check that had answered.**

Read as CONFIRMED when the truth was UNKNOWN:

| Signal | Reality |
|---|---|
| `agent_send` → 200, empty body | prompt accepted by the TUI; nothing ran |
| `heartbeat_at` stale | rendered `running`; a working agent read 102 min stale |
| `auth_failed = None` (never probed) | displayed identically to auth-OK |
| `auth-status` enumerates only running TUI agents | a wedged agent is ABSENT, not alarming |
| image content-assert compared staged-vs-staged | passed a stale image |

Read as REFUTED when the truth was UNKNOWN:

| Signal | Reality |
|---|---|
| `session_jsonl_bytes: 0` | the path was derived from an assumption; the agent was writing elsewhere |
| `git grep -c <symbol>` → 1 | the hit was a comment; a correct PR was blocked as "incomplete" |
| no trailing `# owner` comment on a crontab line | said nothing about manifest membership; a false fleet-wide alarm |

The operator named the root cause in one line:

> 「True (rc=0), False, None を区別しないからおかしいのでは？」

### A ninth failure, of a different and worse kind

The eight above are cases where a signal was READ wrongly. The ninth was
computed RIGHTLY and then thrown away by a type — and it is the mechanism
behind this incident, found in source by scitex-agent-container:

1. `_authheal/_detect.py::evaluate_agents` computes the correct three states —
   `ok` / `auth_failed` / `unknown` — and its docstring explicitly refuses to
   treat `unknown` as a wedge ("absence of evidence is not evidence of a
   wedge"). That reasoning is correct.
2. Its signature is `-> list[str]`. It keeps `auth_failed` and **discards the
   other two**. The ternary existed for the length of one function body and
   then had nowhere to go. **The return type destroyed it.**
3. Reports are built only from that list, so an agent whose pane could not be
   read produces **no report at all**.
4. The exit-code function ends in a bare `return 0`, so "no reports" means
   both *everything was checked and is clean* and *nothing was observed*.
5. The population is `_list_tui_sessions()` — live tmux sessions. An agent
   whose session is gone never becomes a key, so it cannot be reported as
   anything. There is no roster to compare against: **the enumeration IS the
   population, so absence is invisible by construction.**

**This yields a mechanical audit** any package can run: find verdict functions
whose return type cannot express three states (`list[str]`, `bool`), and
exit-code functions with a bare `return 0` fallthrough. Treat each hit as a
sample, not the population.

### The tenth failure: this document's own evidence was false

An earlier draft of this ADR ended the section above with: *"five systemd
timers reported `Result=success ExecMainStatus=0` every ten minutes while an
agent sat login-expired for hours."* **Three of those five units do not
exist.** scitex-agent-container retracted the measurement before merge; the
retraction is kept here rather than quietly edited out, because the way it
failed is the thesis of the document.

The probe was `systemctl --user show -p Result -p ExecMainStatus <unit>`.
Reproduced independently on the host:

```
$ systemctl --user show -p Result -p ExecMainStatus this-unit-does-not-exist.service
Result=success
ExecMainStatus=0            # rc=0

$ systemctl --user is-enabled this-unit-does-not-exist.service
Failed to get unit file state for ...: No such file or directory   # rc=1
```

`show` answers with property **defaults** for a unit it has never heard of. So
a unit that was never installed is indistinguishable from one that ran and
succeeded — `unknown` rendered as the OK pole, by a tool, silently. The ADR
about collapsed ternaries had a collapsed ternary in its evidence.

**The corrected account is stronger than the one it replaces.** Three failures
were stacked, each concealing the one beneath:

1. `-> list[str]` destroyed a correctly-computed ternary (above). Real, and
   fixed — but **not** what kept the agent down.
2. **The remediator had no schedule at all.** Detection ran every ~10 minutes
   and was healthy: 52 passes, 9 of which saw wedged agents. Its verb is
   `cached` — it records the verdict and stops. `login-expired-restart-history.json`
   holds exactly two entries, both stamped when an operator ran `--apply` by
   hand. The agent sat **detected-but-wedged for five and a half hours**, with
   a correct watcher writing the right answer into a file nothing consumed.
3. The defaults-returning probe reported the missing timer as successful,
   which is what allowed (2) to stay invisible while (1) looked like a
   sufficient explanation.

**A WATCHER THAT ONLY RECORDS IS NOT A REMEDIATOR.** Every one of those nine
`cached … (1 wedged)` log lines was accurate. Accuracy was not the problem.

### The instrument test

Naming bad probes one at a time produces a list that is always incomplete and
always one tool behind. The operative test is two questions:

1. **Is this an artifact, or the mechanism's opinion of itself?** An HTTP 200
   is the transport's opinion of itself. `systemctl show` is systemd's opinion
   of a unit that does not exist. A registry's `running` is the registry's
   opinion. A self-report must be *trusted*; an artifact must be *produced*.
2. **Could this artifact exist if the work had NOT happened?** If yes, it is
   not evidence — it is a coincidence you are permitted to have.

Question 2 is not redundant, and the counter-example is a real one: reading
`session_jsonl_bytes: 0` off disk *is* an artifact measurement, and it was
still wrong, because the path was **assumed** and the agent was writing
elsewhere. A missing file at a guessed path is perfectly consistent with the
work having happened. It passes (1) and fails (2).

The ideal case, by contrast: *a nonexistent timer cannot fabricate commits.*
Five hourly snapshot commits with rising task counts could not exist unless
the timer ran.

**Corollary on redundancy.** One conclusion in this incident cited the same
worthless `show` probe and survived anyway, because two independent legs held
(`is-enabled`, and the commit log). Redundancy did not make that conclusion
*more right*. It made the error *non-fatal* — a different and more useful
property than confidence, and the reason to carry a second instrument even
when the first seems sufficient.

## Decision

### 1. THE LAYERING RULE

**Transport success is reported by the transport. Work success is reported by
a receipt. The second is NEVER inferred from the first.**

`200` on a dispatch means "I accepted your request" — true of the wire, silent
about the work. Reading it as "the work happened" is the defect, and no amount
of care prevents a re-occurrence, because the wire's answer is genuinely
correct about the wire.

A corollary, from three instances tonight (`200`, `Error: No such command`,
`session_jsonl_bytes: 0`): **an answer describes the thing that answered, not
the thing you meant to ask about.** `No such command` was true of the binary
reached on a non-login PATH and false of the binary installed; the byte count
was true of the path inspected and false of the agent.

### 2. RECEIPTS ATTEST EFFECT, NEVER DELIVERY

A receipt may only be issued by a party that OBSERVED the work begin. A
receipt issued on transport success is this incident with a certificate
attached.

Only the runtime owner can observe that (it alone sees the session advance),
so the runtime owner issues receipts. `scitex-cards` never does — it has no
way to know, and acquiring one would couple it to a runtime.

### 3. FOUR STATES, AND `pending` IS NOT `unknown`

| State | Meaning |
|---|---|
| `pending` | accepted and in flight; ask again. Carries a deadline. |
| `confirmed` | the issuer OBSERVED the work begin |
| `refuted` | the issuer observed it NOT begin, **with a reason** |
| `unknown` | the issuer could not determine, **with why** |

`pending` MUST age out into `unknown` at its deadline. A receipt stuck
`pending` forever is a failure wearing a progress bar — the same collapse one
layer up.

`unknown` is a first-class, recordable outcome. It is never an absent record,
never folded into `refuted`, never rendered as success.

### 4. STATUS CODES — reuse the standard vocabulary

The operator's observation ("ネットワークの世界では番号で決まってるんじゃないの？")
is correct, and the standard already contains the code we needed:

| Situation | Code |
|---|---|
| dispatch accepted, outcome not yet known | **202 Accepted** (never 200) |
| receipt: work observed to begin | 200 |
| receipt: could not determine | 504 |
| refuted — credential dead | 401 |
| refuted — actor does not exist | 404 |
| refuted — actor alive but unresponsive | 503 |
| duplicate submission | 409 |
| structurally valid, semantically wrong (e.g. `blocked` naming no blocker) | 422 |

Shell boundary mirrors the ternary: exit `0` confirmed, `1` refuted (+reason),
`2` unknown (+why).

Returning **202** for a dispatch is the point: the protocol itself says
"outcome unknown", so the ternary is enforced by the wire rather than by
anyone remembering it. Every rule tonight that depended on discipline had
already failed at least once.

### 5. OWNERSHIP (operator's ruling, 2026-07-18)

> 「scitex-cards は中立に受領証を必要とするかしないかを書いて sac を知らない。
> sac は受領証を作るための仕組みを持つ、状態判定を持つ。」

- **scitex-cards** records neutral facts only: `requires_receipt`, and
  `receipt {by, at, outcome, reason, deadline}`. It knows nothing of TUI,
  tmux, sessions, credentials, or any particular runtime. Reasons are stored
  as OPAQUE strings so the actor's vocabulary can grow without a schema change
  here.
- **The actor** (today: scitex-agent-container) owns state determination and
  receipt production.

This spec is written in terms of an ACTOR, not a named package, because cards
must not depend on a runtime. Any executor may implement it.

## Consequences

- A dispatch can no longer be mistaken for a completion, at the protocol level.
- "We looked and could not tell" becomes visible instead of invisible, which
  is what let an agent sit dead for 80 minutes.
- The board can surface `outcome=unknown`, `aged-out pending`, and
  `receipt absent` as THREE different states, because they are three different
  facts.
- Any check whose "cannot tell" answer equals its success or failure answer is
  unsafe and must be fixed before its verdict is used anywhere.
- **A verdict's TYPE must be able to hold every state the verdict can have.**
  Computing three states and returning a container that expresses one is the
  same defect as never computing them; the ninth failure above is exactly
  that, and no amount of care at the call site can recover a state the return
  type already discarded.
- **An enumeration is not a population.** If the set of things checked is
  built by listing what is currently alive, the broken cases cannot appear in
  it, and their absence will read as health. Compare against a declared roster
  instead.

## A note on comments vs enforcement

Two rules here look contradictory:

- *enforcement beats documentation* — "a warning that must be READ to be true
  is a warning that will be false" (the auth caveat was correct, printed, and
  useless);
- *write down WHY* — a comment explaining why a pin is a SHA, or why one job
  runs on a hosted runner, is what stops the next agent "tidying" it back.

The resolution, and it decides which one carries load: **a why-comment attached
to an ENFORCED constraint is durable; a why-comment attached to nothing is a
wish.**

The live example: sac's hosted-runner exception is not a comment, it is
`.github/hosted-runner-allowlist.yaml` with a machine-checked `reason:` field
(the guard fails if the reason is under 40 characters or the entry goes
stale), enforced three ways — CI job, pre-commit hook, and test. The comment
did not do the work; the allowlist did. The comment made the allowlist
SURVIVABLE by telling the next reader why the entry exists.

So: enforce the constraint, and attach the reason to the enforcement. A
comment alone is not a control, and a control without a stated reason gets
removed by someone who cannot see why it is there.

## Non-goals

- This does not specify HOW an actor observes work beginning; that is the
  actor's business and will differ per runtime.
- This does not define the actor's reason vocabulary. Cards stores reasons
  verbatim and does not interpret them.

<!-- EOF -->
