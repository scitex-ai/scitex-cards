# DESIGN — Remote hub backend: one cards.db, every host (ADR-0011 §11 execution)

**Status:** PROPOSED (workflow-investigated 2026-07-17, three parallel evidence readers: sac reference patterns, GUI API coverage, network surfaces). Implementation on the 0.17 line; sac patterns referenced, zero sac code shared (operator: redundancy is acceptable for independence).


**Status:** proposal for the 0.17 line. **Scope:** spartan + NAS agents read/write the hub's `~/.scitex/cards/cards.db`. **Sources:** sacRef / apiCov / netSurf investigation reports only; report gaps are treated as unknowns and flagged inline.

---

## 1. ARCHITECTURE

### Recommended shape: hub-side RPC service + verb-level backend seam in the package

```
spartan agent                          hub (ywata-note-win)
┌──────────────────────┐               ┌──────────────────────────────────┐
│ Claude Code          │               │ scitex-cards serve (NEW)         │
│  └ stdio MCP server  │               │  bind 127.0.0.1:8765, bearer     │
│    (UNCHANGED tools) │               │  ┌ handlers → _store verbs ──┐   │
│    └ HubBackend (NEW)│── HTTP over ──│──┤ (same flock chokepoint    │   │
│      SCITEX_CARDS_   │  ssh reverse  │  │  the local MCP uses)      │   │
│      HUB_URL         │  tunnel       │  └──────────┬────────────────┘   │
└──────────────────────┘  (hub-init.)  │      ~/.scitex/cards/cards.db    │
                                       └──────────────────────────────────┘
```

- Every remote agent keeps running its **local stdio MCP server** — the 23-tool surface (`_mcp_server.py:264-298`) is byte-identical on remote hosts, satisfying the "identical MCP surface" constraint by construction. Only the storage verbs beneath swap from local-file to HTTP client.
- The hub runs a **new `scitex-cards serve` verb**: a small authenticated HTTP JSON-RPC service in front of the canonical DB. There is exactly one store, opened by processes on one machine — flock + SQLite WAL semantics stay valid (WAL over NFS/SMB is explicitly unsafe per `_db.py:85-119`, which rules out network-filesystem sharing; HTTP-fronting is the only shape compatible with "this one cards.db").

### Existing components to build on

| Component | Verdict |
|---|---|
| Locked `_store` verbs (`_store_write._store_lock`, `_store_mutate`, `_store_lifecycle`, `_store_comment`, `_store_relations`, `_store_reassign`, `_threads.append_message`) | **Foundation.** Every serve handler calls these — the same chokepoint MCP tools use. Full parity, events emitted, flock held across read-modify-write. |
| Django board `/create`, `/comment`, `/chat`, `/dm` handlers | **Pattern only.** These four already delegate to the `_store` chokepoint (crud.py:219-232, 421-429) — that delegation style is the template for every new endpoint. |
| Django board `/update /delete /restore /edge /resolve /reopen /priority /archive` | **Do NOT reuse.** They bypass the lock (cached-board read → `_model.save_tasks`, no `expected_generation`, no card-events — apiCov's documented lost-update window), have zero auth, are csrf_exempt, and honor `?store=` retargeting. Exposing them remotely would be exposing the exact bugs the coverage report catalogued. |
| FastMCP 3.4.4 HTTP transport (`scitex-cards mcp start --http`, verified available) | **Rejected as the primary rail.** A shared hub-side MCP-over-HTTP server resolves `SCITEX_CARDS_AGENT_ID` from *its own* env, so every remote agent's writes would be stamped with one hub identity; and the HTTP transport cannot carry the server-initiated claude/channel push (`_cli/_mcp.py:113-115`). Kept in the back pocket for single-identity tooling, nothing more. |
| `_ports.py` (TaskSyncPort, `host@name` identity contract) | **Reuse the identity contract** (`canonical_agent_id`); TaskSyncPort (git push/pull sync) is a *copies* design — contradicts the operator ruling — leave it dormant. |

### What must be NEW
1. `scitex_cards._backend` — backend protocol + resolver (file vs HTTP), Section 2.
2. `scitex_cards._server` + `scitex-cards serve` CLI — authenticated RPC surface, Section 3.
3. `scitex_cards._backend_http` — the HubBackend client (stdlib urllib, same as `_push.py` — no new deps).
4. Token mint/rotate/provision helpers + `hub doctor`.
5. Hub-initiated reverse-tunnel units (ops, not package code — Section 4).

---

## 2. THE BACKEND SEAM

### Where the split goes: the verb level, not the row level

The apiCov report identifies the chokepoint precisely — the locked verbs the 23 MCP tools call:

- writes: `_store_mutate.add_task` / `update_task`; `_store_lifecycle` (`complete_task`, `delete_task`, `restore_task`, resolve/reopen); `_store_comment.comment_task`; `_store_relations.set_edge` / `set_collaborator` / `set_subscriber`; `_store_reassign.reassign_task` (all holding `_store_write._store_lock` across the full cycle — `_store_write.py:51-90`)
- reads: `list_tasks` / `get_task` / `summarize_tasks` / `poll_notifications`
- sidecars: `_threads` DM verbs; help_wait/help_clear (upserts via the same write verbs)

Introduce `scitex_cards._backend`:

```python
def resolve_backend() -> CardsBackend:
    url = os.environ.get("SCITEX_CARDS_HUB_URL")
    return HubBackend(url) if url else LocalBackend()   # LocalBackend = passthrough to today's _store verbs
```

The `_mcp_*` modules call `backend.<verb>(...)` instead of importing `_store` functions directly. `LocalBackend` is a zero-behavior-change delegation; `HubBackend` maps each verb to one RPC round trip. Coarse verb-level granularity is deliberate: the **server** holds the flock — no distributed locking, no partial-state protocols, one round trip per MCP op.

Three tools stay local-only regardless of backend: `cards_skills_list/get` (bundled package files), and `resolve_store` / `health` become **backend-aware** (`resolve_store` on a remote reports `backend=hub` + the URL — the "am I on a shadow store?" check the operator's ruling needs; `health` adds a hub `/v1/health` + auth probe).

### Env/config contract (all new, zero sac names)

| Variable | Meaning |
|---|---|
| `SCITEX_CARDS_HUB_URL` | Presence flips the backend to HTTP (e.g. `http://127.0.0.1:8765`). Absent → local file, today's behavior exactly. |
| `SCITEX_CARDS_HUB_TOKEN_FILE` | Default `~/.scitex/cards/hub.token` (0600). `SCITEX_CARDS_HUB_TOKEN` env overrides for containers. URL set but no readable token → **hard error at first call**, never a silent local fallback (a fallback would mint the "separate copy" the operator forbade). |
| Serve-side | `scitex-cards serve --port 8765` binds `127.0.0.1` only (no non-loopback flag in v1 at all); auto-mints `~/.scitex/cards/tokens/hub.token` + per-host tokens under `~/.scitex/cards/tokens/<host>.token`, 32 url-safe random bytes, 0600. |

### Identity on the wire
Agent id = `SCITEX_CARDS_AGENT_ID` (already wired per-agent). HubBackend sends it as `X-Scitex-Agent: <host@name>` on every request AND passes it through the verbs' existing `by`/`actor`/`created_by` parameters, so `_log_meta` stamping is identical to local writes. v1 trust model: bearer authenticates the *host*, header declares the *agent* — spoofable between mutually-trusted fleet agents; acceptable now, with per-agent token rows (sac node_tokens *pattern*, own table) as the named v2 hardening. Server rejects any request missing the identity header (fail-loud, mirrors sac's "host-wide bearer honours from_agent verbatim but requires it present").

---

## 3. COVERAGE PLAN — MCP op → HTTP endpoint

All serve endpoints are `POST /v1/rpc/<verb>` (reads included — filter payloads in body), JSON in/out mirroring the verb signatures 1:1 so the mapping is mechanical and testable by generation. "Board twin" = what apiCov found; **none of the twins are reused** — bypass-path twins would import the lost-update bug, and the board has no auth.

| MCP tool | Serve endpoint | Board twin today | Status |
|---|---|---|---|
| add_task | /v1/rpc/add_task | /create (true parity, delegates to _store) | NEW endpoint, EXISTS pattern |
| update_task | /v1/rpc/update_task | /update (bypasses lock — unusable) | NEW |
| complete_task | /v1/rpc/complete_task | none (no _log_meta stamp over HTTP) | NEW |
| get_task | /v1/rpc/get_task | none (docstring's handle_get doesn't exist) | NEW |
| list_tasks (all filters) | /v1/rpc/list_tasks | /tasks (unfiltered 5 MB dump — unusable) | NEW; filters run **hub-side** |
| summarize_tasks | /v1/rpc/summarize_tasks | none | NEW |
| delete_task / restore_task | /v1/rpc/{delete,restore}_task | /delete, /restore (inline, bypass) | NEW |
| comment_task | /v1/rpc/comment_task | /comment (true parity) | NEW endpoint, EXISTS pattern |
| set_edge | /v1/rpc/set_edge | /edge (bypass) | NEW |
| set_collaborator / set_subscriber | /v1/rpc/set_{collaborator,subscriber} | none | NEW |
| resolve_task / reopen_task | /v1/rpc/{resolve,reopen}_task | /resolve, /reopen (bypass, in-process bus only) | NEW |
| reassign_task | /v1/rpc/reassign_task | none (atomic owner change) | NEW |
| help_wait / help_clear | /v1/rpc/help_{wait,clear} | none | NEW |
| poll_notifications | /v1/rpc/poll_notifications | none (inbox is MCP/CLI-only) | NEW |
| dm_send / dm_list | /v1/rpc/dm_{send,list} | /dm/* hardwired from=operator — unusable for agents | NEW |
| health | local + GET /v1/health probe | /ping (bare liveness) | NEW (health endpoint is the one unauthenticated route, sac pattern) |
| resolve_store | local, backend-aware | none | no endpoint needed |
| cards_skills_list / get | local (bundled files) | none | no endpoint needed |

Explicitly absent from serve v1: `?store=` retargeting (the serve store is pinned to the hub canonical, period), priority-reorder/archive (board/CLI-only ops, not in the MCP surface — add later only if a tool appears).

---

## 4. REACHABILITY + AUTH

### The finding that shapes everything
netSurf: spartan→hub and NAS→hub have **no existing inbound rail**. The hub's only inbound path is a RemoteForward 1229 that exists only while an `ssh nas` session is alive; the old autossh cron is dead (bastion decommissioned). But the hub has solid **outbound** reach: plain ssh to spartan's public login node (ControlMaster), and cloudflared to NAS.

### Design: hub-initiated persistent reverse tunnels
The hub pushes the rail out, so no bastion revival, no new inbound firewall surface, no non-loopback bind:

```
hub:  autossh -N -R 127.0.0.1:8765:127.0.0.1:8765 spartan     (systemd unit, Restart=always)
hub:  autossh -N -R 127.0.0.1:8765:127.0.0.1:8765 nas         (rides the existing cloudflared ProxyCommand)
```

Remote agents then use `SCITEX_CARDS_HUB_URL=http://127.0.0.1:8765` — a loopback URL on their own host that *is* the hub API. Transport encryption is the ssh channel; the API itself never leaves a loopback interface on either end. This is exactly the in-house `scitex-ssh` primitive (autossh + systemd `-R` units), pointed at port 8765 instead of 22 — generalize its unit template rather than writing a new tool.

**Known limits (from report gaps, not assumptions):** spartan compute nodes (`spartan-*`) can't reach the login node's loopback — pilot scope is login-node agents only; extension options (GatewayPorts on the remote forward, or a login-node relay) are deferred. Whether WSL systemd user units are live on the hub was not verified — the tunnel unit lands as ops verification in the pilot, with a supervised-loop fallback.

### Token design + operator provisioning
- Hub mints per-host tokens: `~/.scitex/cards/tokens/{spartan,nas}.token` (0600, 32 random url-safe bytes, constant-time compare server-side, `/v1/health` public — all sac *patterns*, zero sac code or files).
- One-time provisioning per host, shipped as a helper: `scitex-cards hub provision spartan` → scp token to `spartan:~/.scitex/cards/hub.token` (0600) over the existing ssh alias.
- Operator must provision: (1) the two tunnel units on the hub, (2) run `hub provision` twice, (3) set `SCITEX_CARDS_HUB_URL` in remote agents' environments (how it's injected is the deployer's concern — cards stays independent of sac's injector). Nothing else.
- `scitex-cards hub doctor` (remote side): URL set? token readable? `/v1/health` reachable? authenticated identity echo matches `SCITEX_CARDS_AGENT_ID`? Four checks, each fail-loud with a hint.

---

## 5. PATTERNS FROM SAC — adopted vs deliberately not

**Adopted (as patterns, re-implemented; CI gate keeps zero sac imports):**
1. *Loopback-only bind, tunnels as the sanctioned transport* (sac default 127.0.0.1:7878, non-loopback behind an explicit flag) — serve is loopback-only, v1 has no override flag at all.
2. *Auto-minted 0600 bearer token file, constant-time compare, health route public* (sac tokens.py / auth.py).
3. *Caller holds the destination's token, missing token fails loud* (sac peer-tokens) — inverted direction: hub mints, remotes hold.
4. *Identity rides the request and the server requires it; bearer-vs-identity anti-spoof binding* (sac message:send ACL) — v1 requires the header; v2 adds per-agent token rows (node_tokens pattern).
5. *JSONL audit line per remote write* (sac host_exec audit) — `~/.scitex/cards/logs/hub_access.log`.
6. *Env-pair contract for in-container clients* (`SAC_LISTEN_BASE_URL`/`SAC_LISTEN_BEARER` shape → `SCITEX_CARDS_HUB_URL`/token file, our names).
7. *Never do blocking network work on the serve/bind path* (sac's 2026-06-26 pre-bind-ssh hang incident) — serve does no outbound calls at startup, ever.

**Not adopted, with reasons:**
1. *ssh + remote-curl per request* (`_post_via_ssh_curl`) — an ssh exec per MCP call is wrong for a hot data plane, and the remote→hub ssh direction barely exists (chained, fragile). Persistent tunnel + plain HTTP instead.
2. *Symmetric per-host DBs + pull aggregation* (`db export/import`, orochi pulls) — this is the "separate copies" model the operator explicitly ruled out. One DB, one host, remote clients.
3. *comms_nodes anti-entropy federation* — there is exactly one hub; nothing to federate.
4. *Lineage/group ACL mesh* (check_spawn, check_lineage_acl) — cards has no lifecycle/spawn semantics; a flat authenticated-fleet trust model suffices, per sac's own 2026-07-03 finding that the security boundary belongs on dangerous ops, not messaging.
5. *host_exec-style arbitrary command surface* — serve exposes only the closed verb set; no generic execution, no `?store=` path selection.

---

## 6. STAGED PRs + SPARTAN PILOT

Sequencing rule honored: PR-1..4 touch only **new modules** plus import-swaps in `_mcp_*`; they never modify `_store_*` internals, so the in-flight S6 store cutover (YAML→SQLite beneath the verbs) proceeds independently — the seam sits *above* the engine S6 is swapping. Land after S6's verb signatures are frozen on 0.17.

| PR | Content | Independent verification |
|---|---|---|
| **PR-1** `_backend` seam | Protocol + `LocalBackend` passthrough + resolver; `_mcp_*` route through it. Zero behavior change. | Entire existing test suite green unchanged; new unit test asserts LocalBackend returns the identical objects the `_store` verbs return for every verb. |
| **PR-2** `scitex-cards serve` | RPC endpoints (Section 3 table), bearer middleware, identity requirement, token mint/rotate, JSONL audit, `/v1/health`. Handlers call `_store`/`_threads` verbs only. | Pytest boots serve on an ephemeral port: 401 without/with-bad token; full endpoint matrix; **every write followed by a direct store read-back asserting the mutation + `_log_meta` attribution**; concurrent-writer test (two clients, interleaved update_task, no lost update). |
| **PR-3** `HubBackend` client | urllib client, env contract, fail-loud errors (connection refused → "hub tunnel down?", 401 → "token"), backend-aware `resolve_store`/`health`. No fallback-to-local path exists in the code. | Loopback integration test: serve + `SCITEX_CARDS_HUB_URL` client in one CI job; run the full MCP verb matrix through HubBackend; assert read-back equality against a direct hub-side store read; assert URL-set-token-missing hard-errors. |
| **PR-4** Provisioning + doctor | `hub provision <host>`, `hub doctor`, tunnel unit template (scitex-ssh-style), docs, CI gate confirming zero `scitex_agent_container` imports. | Doctor unit tests (each failure mode → distinct hint); template lint; import-gate red/green test. |

**Spartan pilot (ops step, gates 0.17 GA of the feature):**
1. Operator: tunnel unit + `hub provision spartan` + env on one login-node agent.
2. `scitex-cards hub doctor` on spartan: all four checks green.
3. Write matrix from spartan via ordinary MCP tools: `add_task` (unique id) → `comment_task` → `update_task` → `set_edge` → `dm_send` → `poll_notifications(ack)`.
4. **Read-back gate — every claim verified from the other side:** each write is confirmed by (a) hub-side direct read (CLI/sqlite against `~/.scitex/cards/cards.db`) showing the row with `created_by`/actor = the spartan agent id, and (b) spartan-side `get_task` returning the identical card. No write counts until both read-backs pass.
5. Record per-op latency over the tunnel (expect 1 RTT/op); kill the tunnel mid-session and confirm the failure is loud, attributed, and leaves no local shadow store on spartan.
6. NAS follows only after the spartan gate passes.

---

## 7. RISKS & MITIGATIONS

| Risk | Mitigation |
|---|---|
| **Concurrency on one cards.db** — HTTP writers + hub-local MCP + board writing simultaneously | Serve handlers use only the locked `_store` verbs (flock across full read-modify-write, SQLite WAL + 300s busy_timeout hub-side) — the server is just another local process, so the existing single-host locking story covers it. Residual hazard is the board's 8 bypassing endpoints (apiCov's lost-update finding) — pre-existing, hub-local, and a recommended follow-up card: route them through `_store` too. They are never exposed remotely. |
| **Latency** — every MCP op is a WAN round trip (hub↔spartan) | One RPC per op by design; `list_tasks` filters execute hub-side (never the 5 MB `/tasks` dump); no auto-retry on writes (avoids duplicate comments; `add_task` is id-idempotent anyway); pilot records p50/p95 before NAS rollout. |
| **Auth leakage** — token on a shared HPC home dir | 0600 file, never in URL/query, only the Authorization header, wire is ssh-encrypted end to end; `serve --rotate-token` + re-provision is a two-command rotation; JSONL audit gives detection. Agent-identity spoofing within the trusted fleet is accepted v1, closed by per-agent tokens in v2. |
| **Tunnel fragility** — remote work stalls when the forward drops | autossh Restart=always; end-to-end monitor = hub cron ssh-probing `curl 127.0.0.1:8765/v1/health` *on the peer* (traverses the whole loop, hub-initiated like sac's pull posture). Deliberately **no offline write queue**: a queue is a second copy under another name — remotes fail loud and wait, per the operator's one-database ruling. |
| **Partial rollout skew** — a remote still writing a local store diverges | Per-host cutover: set env, one-time import of any residual local cards, then archive/rename the remote store file so nothing can write it; `resolve_store` prints `backend=hub` as the agent-verifiable proof; fleet check greps for surviving remote store files. |
| **S6 entanglement** | Seam is verb-level and additive; PRs never touch `_store_*` bodies; land after S6 signature freeze on 0.17. If S6 slips, PR-1/2 still merge (they exercise whatever engine is current). |
| **Serve availability / lock queueing** under many writers | busy_timeout + flock serialize correctly (correctness before throughput); serve runs verbs on a bounded worker pool; `/v1/health` + audit log give the p95 signal; scale-out is a non-goal — one hub, one DB, by ruling. |
| **Unknowns carried from reports** | Spartan/NAS-side ssh configs and live tunnel state unprobed → pilot step 1 verifies empirically; fastmcp versions on remotes irrelevant (remotes run stdio as today); WSL systemd availability verified during tunnel-unit install, loop fallback ready. |