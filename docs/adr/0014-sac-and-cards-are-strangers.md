# ADR-0014 — scitex-cards and scitex-agent-container are strangers

**Status:** PROPOSED (operator-directed 2026-07-20)
**Owner:** scitex-cards
**Counterpart:** sac's ADR-0022 records the same separation from their side.
Neither document is authoritative over the other, and that is the point —
authoring the other project's ADR would violate the boundary being recorded.
**Amends:** ADR-0007 (scopes it — see *The contract is a serialization schema*)

## Context

These two packages have been coupled in practice while claiming to be
independent. The operator settled the question directly:

> 「SACはエージェント、カードはあくまでもユーザと言うことで分離してるってことですね」
> sac is agents; cards is users — that is the separation.

> 「それぞれ全くの他人、知らない人同士」
> They are complete strangers, people who do not know each other.

This ADR records what that means for **this** package.

## The distinction that makes it principled

Both systems track something that could be called "state", and that surface
similarity is what kept pulling them together. The separation is not about
avoiding duplication; it is about **different subjects**:

| | subject |
|---|---|
| scitex-agent-container | **agents** — lifecycle, liveness, agent-to-agent comms |
| scitex-cards | **users** — task-centric work, human-facing |

Two systems tracking state are not duplicates when the subjects differ. That
single sentence is the whole justification, and without it the separation would
be arbitrary taste.

## Decision

**1. Overlap is accepted, not consolidated.**

> 「重複があっても意味のある重複リダンダンシーだと思ってます」
> Even where there is duplication, I consider it meaningful redundancy.

We do **not** extract a shared library for concepts appearing in both. A shared
library is a shared failure: it converts two independent outages into one
correlated outage, and it re-creates the coupling by a different name.
Duplication that buys independence is paid for deliberately.

This also settles a question that has recurred: the DM rail exists in both
projects. That is intended, not debt. The operator ruled on it explicitly in
July: 「二重になっているのは構いません」 — the duplication is fine.

**2. No imports, in either direction.**

`scitex_cards` must not import `scitex_agent_container`, and must not shell out
to `sac`. This is already a standing CI invariant (ADR-era work, #461): an AST
scan of every module plus a runtime probe asserting no forbidden module loads
laterally. This ADR states the *reason* the invariant exists rather than
restating the mechanism.

**3. An adapter, if one is ever needed, is a third thing.**

Owned by neither project, depending on both, releasable on its own cadence.
Neither package grows knowledge of the other in order to get one.

**4. The contract is a serialization schema, not a language-native type.**

Where any producer hands data to any consumer across a project boundary, the
contract is the serialized shape — not a Python class.

This **scopes ADR-0007**, which made `scitex_cards._model.Task` the canonical
schema source. That decision was about *internal* consistency: one source for
the validator, the UI render contract, and the adapters, so four encodings of
one schema cannot drift. That reasoning is unchanged and ADR-0007 stands for
everything inside this package.

What is added here is the boundary rule: a dataclass as an *external* contract
silently restricts every consumer to Python. Inside the package, the dataclass
is the source. Crossing a project line, the serialized schema is the source.

## Why separation is the cheaper option, not the principled-but-costly one

> 「区切っておくと楽なんですよね。管理もテストも」
> Keeping them separated is easier — management and testing both.

Each project keeps its own release cadence, its own test suite, and its own
failure domain. The argument for separation is not purity; it is that the
ordinary work gets easier.

## Evidence: what the coupling actually cost

Every item below is a real failure from 2026-07-16 → 2026-07-20, and each traces
to one project knowing something about the other that it should not have.

- **The fleet became unstartable.** sac stages skills through symlinks naming a
  path *inside* this package (`src/scitex_cards/_skills/<name>`). Renaming that
  directory dangled the link and every `sac agents start` died in
  `shutil.copytree` with `[Errno 2]` — four agents down at once, twice nearly
  repeated. An external consumer held a reference to our internal layout.

- **Host configuration re-stamped our database.** A dotfiles-wide environment
  pin named the legacy store path, so host processes resolved it, wrote, and
  re-stamped the database away from container agents — twice within five
  minutes, locking this agent out of its own cards while all 2,181 rows sat
  healthy underneath.

- **sac's stop hook read our store to decide whether it may stop**, against a
  stale representation: it reported 20 open items while the store held 8.
  A liveness decision in one project depending on a second project's data
  layout is the coupling in its clearest form.

- **sac's test suite reached our live store**, and their alarm modules
  manufactured a decoy store from a "no store means no card" conflation.

- **The two agents worked in lockstep** — waiting on each other's windows,
  co-scheduling merges. That is the same coupling expressed in human form, and
  it was the slowest of all of them.

The pattern: none of these were caused by a shared library. They were caused by
one project *knowing a fact* about the other — a path, an env name, a data
shape, a schedule.

## Consequences

- Cross-project changes stop being coordinated windows and become independent
  releases. The concrete instance: a compat symlink shipped *inside* this repo
  removed the need for a synchronized skills-rename window entirely. Removing
  the need to coordinate beats coordinating well.
- Some facts get encoded twice. Accepted.
- When the two must interact, the interaction is a documented serialized
  contract or a third-party adapter — never an import, a path reference, or an
  assumed schedule.

## What this ADR does not claim

It does not claim the separation is complete. As of writing, sac still resolves
`_skills/` paths inside this package, and the manifest skill IDs are still
`scitex-todo-*` and declared in every agent spec. Those are coordinated
migrations, not cleanups, and they are open. This document records the target
and the reasoning, not a finished state.
