#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun ``scitex-cards hub`` — remote-host provisioning + diagnosis (PR-4).

docs/design/remote-hub-backend.md §4: the operator provisions a remote host
in two moves — a tunnel unit (docs/ops template) and ``hub provision <host>``
— after which the remote's agents set ``SCITEX_CARDS_HUB_URL`` and every MCP
verb rides the hub rail. ``hub doctor`` is the remote-side four-check
diagnosis, each failure carrying an actionable hint (constitution §2).

Verbs:

- ``hub provision <host>`` (HUB-side): mint ``tokens/<host>.token`` (0600)
  and scp it to ``<host>:~/.scitex/cards/hub.token`` over the operator's
  existing ssh alias. Fail-loud on a nonzero scp — a token that did not
  land is a rail that does not exist.
- ``hub doctor`` (REMOTE-side): (1) URL set? (2) token readable?
  (3) ``/v1/health`` reachable? (4) authenticated ``/v1/whoami`` echo equals
  this agent's identity? Exit 0 only when all four pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

import click


def register(main: click.Group) -> None:
    """Attach the ``hub`` noun to the root group."""
    main.add_command(hub_group)


@click.group("hub", help="Hub-rail provisioning + diagnosis (remote store rail).")
def hub_group() -> None:
    pass


@hub_group.command(
    "provision",
    help=(
        "HUB-side: mint a per-host bearer token and scp it to the remote's "
        "~/.scitex/cards/hub.token (0600 both ends) over the existing ssh "
        "alias.\n\nExample:\n  scitex-cards hub provision spartan"
    ),
)
@click.argument("host")
def provision_cmd(host: str) -> None:
    from scitex_cards import _server

    token_path = _server.mint_token(_server.default_tokens_dir(), host)
    remote_dir = "~/.scitex/cards"
    # Two commands, both loud: ensure the remote dir exists, then land the
    # token at 0600. scp/ssh use the operator's ssh config (alias, keys,
    # ProxyCommand) untouched — the rail rides existing trust, adds none.
    steps = [
        ["ssh", host, f"mkdir -p {remote_dir} && chmod 700 {remote_dir}"],
        ["scp", "-p", str(token_path), f"{host}:{remote_dir}/hub.token"],
        ["ssh", host, f"chmod 600 {remote_dir}/hub.token"],
    ]
    for argv in steps:
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            raise click.ClickException(
                f"provision {host}: `{' '.join(argv)}` exited "
                f"{proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
                "\nHint: verify `ssh " + host + "` works from this shell; the "
                "provision rides the operator's existing ssh alias/config."
            )
    click.echo(
        f"provisioned {host}: {token_path.name} -> {host}:{remote_dir}/hub.token "
        "(0600). Next: the tunnel unit (docs/ops/) and "
        "SCITEX_CARDS_HUB_URL=http://127.0.0.1:8765 in the remote agents' env."
    )


def _doctor_checks(url: str | None, agent: str | None) -> list[dict]:
    """The four checks, pure-ish and orderable — returns [{name, ok, detail, hint}]."""
    from scitex_cards._backend_http import _resolve_token

    checks: list[dict] = []

    ok = bool(url)
    checks.append(
        {
            "name": "hub_url_set",
            "ok": ok,
            "detail": url or "SCITEX_CARDS_HUB_URL is unset",
            "hint": ""
            if ok
            else "export SCITEX_CARDS_HUB_URL=http://127.0.0.1:8765 "
            "(the loopback end of the hub-initiated reverse tunnel).",
        }
    )

    token = None
    try:
        token = _resolve_token()
        checks.append(
            {
                "name": "token_readable",
                "ok": True,
                "detail": "token resolved",
                "hint": "",
            }
        )
    except Exception as exc:  # noqa: BLE001 — the error text IS the diagnosis
        checks.append(
            {
                "name": "token_readable",
                "ok": False,
                "detail": str(exc),
                "hint": "run `scitex-cards hub provision <this-host>` ON THE HUB.",
            }
        )

    if url:
        try:
            with urllib.request.urlopen(f"{url}/v1/health", timeout=10) as resp:
                payload = json.loads(resp.read())
            checks.append(
                {
                    "name": "health_reachable",
                    "ok": bool(payload.get("ok")),
                    "detail": f"health {payload}",
                    "hint": "",
                }
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            checks.append(
                {
                    "name": "health_reachable",
                    "ok": False,
                    "detail": str(exc),
                    "hint": "is the reverse tunnel up? (systemctl --user status "
                    "scitex-cards-hub-tunnel; the hub end runs `scitex-cards serve`).",
                }
            )
    else:
        checks.append(
            {
                "name": "health_reachable",
                "ok": False,
                "detail": "skipped: no URL",
                "hint": "set SCITEX_CARDS_HUB_URL first.",
            }
        )

    if url and token and agent:
        try:
            req = urllib.request.Request(
                f"{url}/v1/whoami",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Scitex-Agent": agent,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                echoed = json.loads(resp.read()).get("agent")
            ok = echoed == agent
            checks.append(
                {
                    "name": "identity_echo",
                    "ok": ok,
                    "detail": f"sent {agent!r}, hub echoed {echoed!r}",
                    "hint": ""
                    if ok
                    else "something between this client and the "
                    "hub rewrites headers — the declared identity must survive "
                    "the transport intact.",
                }
            )
        except urllib.error.HTTPError as exc:
            checks.append(
                {
                    "name": "identity_echo",
                    "ok": False,
                    "detail": f"HTTP {exc.code}",
                    "hint": "401 → the token was rotated on the hub; re-provision "
                    "this host. 400 → no identity: set SCITEX_CARDS_AGENT_ID.",
                }
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            checks.append(
                {
                    "name": "identity_echo",
                    "ok": False,
                    "detail": str(exc),
                    "hint": "see health_reachable.",
                }
            )
    else:
        missing = "URL" if not url else ("token" if not token else "agent identity")
        checks.append(
            {
                "name": "identity_echo",
                "ok": False,
                "detail": f"skipped: no {missing}",
                "hint": "set SCITEX_CARDS_AGENT_ID."
                if missing == "agent identity"
                else "fix the earlier checks first.",
            }
        )
    return checks


@hub_group.command(
    "doctor",
    help=(
        "REMOTE-side: diagnose the hub rail — URL set, token readable, "
        "/v1/health reachable, authenticated identity echo intact. Exit 0 "
        "only when all four pass; every failure carries its fix.\n\n"
        "Examples:\n  scitex-cards hub doctor\n  scitex-cards hub doctor --json"
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Raw JSON report.")
def doctor_cmd(as_json: bool) -> None:
    url = os.environ.get("SCITEX_CARDS_HUB_URL")
    agent = os.environ.get("SCITEX_CARDS_AGENT_ID")
    checks = _doctor_checks(url, agent)
    ok = all(c["ok"] for c in checks)
    if as_json:
        click.echo(json.dumps({"ok": ok, "checks": checks}, indent=2))
    else:
        for c in checks:
            mark = "✓" if c["ok"] else "✗"
            line = f"{mark} {c['name']}: {c['detail']}"
            if not c["ok"] and c["hint"]:
                line += f"\n  hint: {c['hint']}"
            click.echo(line)
        click.echo("hub doctor: ALL OK" if ok else "hub doctor: FAILING")
    raise SystemExit(0 if ok else 1)


# EOF
