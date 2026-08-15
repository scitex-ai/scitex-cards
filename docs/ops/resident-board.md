# The board is a resident service on every host

## The incident this exists to prevent

On 2026-08-14 the operator opened the card board and got a bare
`ERR_CONNECTION_REFUSED`. Nothing was listening on `:8051` — not on his laptop,
not on the host the GUI agent had been moved to. The board had been serving
nowhere for hours.

Everything else was green. The card store was resident
(`scitex-cards-pg.service`). The agent that maintains the GUI was alive, with a
fresh heartbeat. Every liveness instrument in the fleet reported health while
the one surface the operator actually reads was absent, and the only thing that
had ever started it was a human running a startup script by hand.

That is the failure shape worth naming: **a process nobody is responsible for
starting has no failure mode, only an absence** — and an absence is invisible
until someone goes looking. The board had been declared the fleet's primary
channel that same night.

## Install it on every host

```bash
scitex-cards board install-service
systemctl --user daemon-reload
systemctl --user enable --now scitex-cards-gui.service
```

`install-service` writes the unit and prints those two commands; it never runs
systemctl itself. Enabling a service on a host stays a human decision — but it
is a decision made **once per host**, not once per boot, which is the whole
point of having a unit.

Verify with an HTTP status, never with "the process exists":

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8051/   # → 200
```

### Check lingering, or the unit is a promise it cannot keep

`WantedBy=default.target` starts the unit when the **user session** starts. On a
headless host nobody logs into interactively, that never happens — the unit
would sit enabled and dead through every reboot, which is the same silence this
whole thing exists to end, arrived at by a different road.

```bash
loginctl show-user "$USER" -p Linger      # → Linger=yes
sudo loginctl enable-linger "$USER"       # if it says no
```

`scitex-compute-04` already has `Linger=yes` (checked 2026-08-15), which is why
`sac-listen.service` and `scitex-cards-pg.service` survive reboots there. Check
it on any host before believing the board will come back on its own.

## Every host, on loopback — not one host over the VPN

The board runs on **every** host, bound to `127.0.0.1`. What travels between
hosts is the **data**, over each host's `:55432` Postgres and its existing
sync.

The tempting alternative — serve the board once and reach it over the VPN — was
proposed and rejected by the operator on 2026-08-14:

> 「vpn から届くというのはおかしくて、各ホストで立ち上がる、55432 でデータを同期
> する、が正しいです」
> 「一つの場所を見ると単一障害点になったり、vpn が切れると見れなくなったりして
> しまいます」

One place to look is a single point of failure and dies with the network. The
unit therefore never widens the bind, and `--host` exists for local
experiments, not for exposure.

Keep the two questions separate, because conflating them is what produced the
outage:

| question | answer |
| --- | --- |
| where does the GUI **agent** live? | one host — wherever its spec says |
| where does the **service** run? | every host, on loopback |

## What the unit does, and why

| line | why |
| --- | --- |
| `Restart=always` | The board's **absence** is the fault, however it went away — an OOM sweep, a stray `gui stop`, a closed parent terminal. `on-failure` (which the notify daemon uses) would leave a cleanly-exited board down. Stop it with `systemctl --user stop`. |
| `ExecStart=... --force` | `gui serve` refuses to start when the pidfile names a live process. For a human that refusal is right; for the resident service it is a trap — one leftover pidfile and the unit fails on every restart forever. `--force` is a documented takeover, and a no-op when nothing is running. |
| absolute `ExecStart` | systemd does not run units through a login shell and does not inherit `$PATH`; the console script lives in a venv. A bare command dies at `203/EXEC`. The path is resolved at install time, and the install **refuses** rather than write a unit that cannot start. |
| no `Environment=` | The store resolves from `~/.scitex/cards/config.json` with no environment at all (verified under `env -i`). A unit depending on `$SCITEX_CARDS_DB` from `~/.bashrc` would start, refuse the unconfigured store and crash-loop — and the store has exactly one identity, so a unit file must not become a second place it is declared. |
| no dependency on the Postgres unit | The store may be Postgres on one host and a file on another, and that unit is owned by another package. If the store is not up yet, the start simply fails and `Restart=always` retries. |

## The absence is now loud

`scitex-cards health` grows a `gui_resident` check. It reads two facts — is a
unit installed (a **declaration**), and is anything listening — and reports
four states:

| declared | listening | verdict |
| --- | --- | --- |
| yes | yes | pass |
| yes | no | **fail** — this is the 2026-08-14 fault, with the restart command as the hint |
| no | yes | pass, and says the board was hand-started and will not survive a reboot |
| no | no | **unknown** — this host never promised a board; named in the summary, never a silent pass |

"Nothing is listening" is only a fault against a declaration. A check that
failed on every container in the fleet would be switched off within a day,
which is how a real alarm gets lost. The check probes the port the installed
unit actually declares, so a host installed on a custom port is not told a
confident story about `:8051`.
