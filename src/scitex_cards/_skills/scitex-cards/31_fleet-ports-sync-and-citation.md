---
description: |
  [TOPIC] Fleet ports, cross-host sync, citation and the auto-merge poll
  [DETAILS] The ports-and-adapters backbone, cross-host SSH-fanout liveness, the task citation scheme, subscriber notification, and the canonical auto-merge poll definition (ADR-0006).
tags:
  [
    scitex-cards-fleet-ports-sync-and-citation,
    scitex-cards-architecture,
    scitex-cards-conventions,
  ]
---

# Fleet ports, cross-host sync, citation and the auto-merge poll

Split out of [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md) to keep both leaves inside the
skill-size budget. That file remains the entry point.

## Architectural backbone — `scitex-cards` is STANDALONE; the fleet plugs in via PORTS

Operator's defining rule (TG 9678, lead a2a `fae53b8e`):

> "scitex-cards はそれだけで独立したパッケージであるべきで、他を知らないが、
> extension port は持っている"
> (scitex-cards MUST be standalone, knows nothing about fleet/sac/
> scitex-specifics, but exposes extension ports through which
> fleet-specific behaviour plugs in.)

This skill describes the *conventions* an adopting agent must follow.
Those conventions are wired through extension ports — the **core
package never imports sac, a2a, SSH-fanout, or the 6-stream list**.
Reading this skill, mentally replace:

- "sync via git + GitHub" ⇒ "the TaskSyncPort impl your fleet installs"
- "publish on `scitex-cards:task:<id>`" ⇒ "NotificationPort.publish"
- "SSH-fanout to peer hosts for liveness" ⇒ "LivenessPort.list_agents"
- "sac fleet groups gate ACL" ⇒ "IdentityACLPort answers"

The fleet-specific implementations live in a SEPARATE package
(e.g. `scitex-cards-fleet` or in the scitex-agent-container glue),
not inside `scitex-cards`. ADR-0006 in `docs/adr/` has the four port
Protocol definitions + the dependency-injection wiring. This skill
documents the **adoption-side** conventions; the **core-side**
contract is in the ADR.

A standalone `pip install scitex-cards` ships with default
implementations (LocalFileSync / InProcessPubSub / NullLiveness /
OpenACL) so the package is independently usable. The conventions
below describe the FLEET deployment shape — operator-host
running aggregator + watcher + board, agents writing project
tiers, etc.
## Task referencing / citation — `<project>/<local-id>` (ADR-0006)

Stable, fleet-wide task ids use a slash-separated two-segment form:

```
<project>/<local-id>
```

- `<project>` = the project's directory basename (matches `Task.project`).
- `<local-id>` = the agent's chosen string, unique within the project.

Examples:

```
paper-scitex-clew/cohort-a-rerun
scitex-hub/decide-prod-cutover-final-go
scitex-cards/proj-scitex-cards-fleet-liveness
```

**URL scheme** (the board's Django serves it):

```
http://<board-host>:8051/task/<project>/<local-id>       # canonical
http://<board-host>:8051/t/<project>/<local-id>          # short alias
```

Operator / lead / agents cite this URL in chat / a2a / comments.
Pasting it into a markdown comments[] entry auto-links via the
board's renderer. Citation in chat: "see
`paper-scitex-clew/cohort-a-rerun`" is unambiguous across the
fleet.

**Backward-compat**: existing single-segment ids
(`proj-scitex-cards-compute-state-deps`) carry through; the
aggregator stamps `_log_meta.canonical_id = "<project>/<id>"` on
read so the URL works on legacy rows.
## Update → subscriber notification — reuse a2a/channel push (ADR-0006)

When a task is updated (via the board's `_store.update_task`, an
agent's direct YAML edit + commit, or the operator's Resolve button),
the change is **published on the sac channel bus** the fleet already
uses for agent wake-ups:

```
event channel: scitex-cards:task:<project>/<local-id>
payload:       {task_id, changes, ts, actor}
```

**Subscription rules**:

| Subscriber | Subscribes to | Action on receive |
|---|---|---|
| owning agent | `scitex-cards:task:<own-project>/*` | wakes (empty-beacon-fix + wake-generalize) + acts on the change |
| dependent agent | `scitex-cards:task:<each-of-its-depends_on-ids>` | wakes + re-evaluates readiness; auto-unblock if a dep flipped to done |
| UI (every viewer) | `scitex-cards:task:*` (filtered client-side) | re-fetches /graph + re-renders affected card / panel |
| lead | `scitex-cards:task:*` firehose | logs into _log_meta; no auto-action |
| operator | (interacts via UI; UI is the subscriber) | UI surfaces the change visually |

**Critical synergy**: this rides the SAME push infra being hardened
by the empty-beacon fix (proj-scitex-agent-container) + the
wake-generalize (any-channel-wakes-idle-agent). scitex-cards is one
of the loadiest consumers — every task update is a potential
agent-wake event. **Do NOT invent a parallel notification system.**

**Fallback to polling**: if the push bus is down, subscribers fall
back to the existing 5s `/rev` polling (AutoRefresh.tsx). Push is
the FAST path; poll is the durable path. Same shape as the
GitHub-vs-SSH-fanout split above.
## Cross-host reach — SSH-fanout liveness (ADR-0006)

The database itself is the single canonical store (no per-host copies
to reconcile). Cross-host pieces that DO still fan out over SSH:

- **Fleet liveness** (`agents.json`) — rebuilt every ~5s by the
  sac-status-writer sidecar from SSH-fanout polls of peer sac
  registries; feeds the board's `/agents` panel. A peer that doesn't
  answer is flagged UNREACHABLE, not silently omitted.
- **`db export`/`db import`** — the cross-host pull path for a peer
  that cannot reach the canonical database directly (see `sac db
  export` / `sac db import`).
## Canonical auto-merge poll — CI-green = `{CLEAN, UNSTABLE}`

Lead a2a `9c4d3dc4` (2026-06-07): a wedge discovered on PR #52 + #55
because the auto-merge poll only treated `CLEAN` as terminal. Both
PRs sat at `UNSTABLE` for 85+ minutes — required checks were green,
but a NON-required check was failing, so the poll slept forever.

**Canonical rule** (every fleet auto-merge loop should treat this
the same — capture for clew / neurovista / ripple / etc. patterns):

> The operator's standing **"CI-green ⇒ auto-merge"** authorization
> applies when `mergeStateStatus` is in `{CLEAN, UNSTABLE}`.
> `UNSTABLE` means a NON-required check is red while all required
> checks pass, which is still mergeable per branch protection.

Bash shape (the bug-fixed version every auto-merge poll should use):

```bash
until s=$(gh pr view "$PR" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null); \
        [ "$s" = "CLEAN" ] \
     || [ "$s" = "UNSTABLE" ] \
     || [ "$s" = "DIRTY" ] \
     || [ "$s" = "BLOCKED" ] \
     || [ "$s" = "HAS_HOOKS" ] \
     || [ "$s" = "BEHIND" ]; do
    sleep 30
done
# Terminal-mergeable: CLEAN or UNSTABLE → gh pr merge --squash
# Non-mergeable terminal: DIRTY (conflicts) / BLOCKED (required check
# failed) / BEHIND (base moved) / HAS_HOOKS (commit hooks failing) →
# investigate, don't auto-merge.
```

**Hygiene caveat** (lead a2a `9c4d3dc4`): `UNSTABLE` hides WHICH
non-required check is failing. Auto-merge on it is correct, but if
the SAME check is unstable across many PRs, the check is probably a
real signal we're ignoring and should be PROMOTED to required.
Flag the pattern — don't let "always UNSTABLE" become invisible.

This pattern is canonical for every fleet auto-merge loop, not just
scitex-cards's. clew / neurovista / ripple / etc. that ship their own
wait-on-CI auto-merge loops should match this shape. Documented here
as the reference for the fleet's dogfood-of-scitex-cards adoption.
