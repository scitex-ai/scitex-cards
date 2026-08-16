#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The notifyd pidfile FORMAT + a namespace-agnostic liveness verdict.

Why this module exists (the cross-namespace false-negative)
----------------------------------------------------------
notifyd runs on the BARE HOST (systemd), while fleet agents run in CONTAINERS
that share the task store through a bind-mount. The store — and therefore the
pidfile — crosses the container boundary. The PID INSIDE IT DOES NOT.

**A pid is only meaningful within the PID namespace that issued it.** The host's
pid 3285778 simply does not exist in the container's ``/proc``, so an
``os.kill(pid, 0)`` probe from inside the container raises ``ProcessLookupError``
and the health check concluded the daemon was DEAD — while it was demonstrably
alive and ticking on the host. A check that reports FAILED on a healthy system,
permanently, is worse than no check: it trains every reader to ignore the
channel, so a real death goes unnoticed.

The identity that actually matters is the PID NAMESPACE, not the hostname
-------------------------------------------------------------------------
Hostname is NOT a sufficient discriminator: apptainer (the fleet's runtime)
shares the UTS namespace by default, so ``socket.gethostname()`` inside the
container returns the SAME name as the bare host — a hostname-only stamp would
still have concluded "this pid is mine to interpret" and still been wrong. We
therefore stamp, and compare on, ``/proc/self/ns/pid`` (the namespace inode),
with hostname + boot id as corroborating (and human-readable) evidence.

The contract
------------
* SAME namespace ⇒ probe the pid with ``os.kill(pid, 0)``. It is the sharpest
  signal available and stays exactly as it was.
* DIFFERENT (or undeterminable) namespace ⇒ NEVER conclude death from the pid.
  Judge by HEARTBEAT FRESHNESS: the daemon rewrites its pidfile every tick, so a
  stamp younger than a few tick intervals means alive; a clearly stale one means
  dead (fail-loud, with an actionable hint).
* If freshness cannot be established either (a LEGACY pidfile from a pre-
  heartbeat notifyd), say so truthfully rather than inventing a verdict.

Format (backward compatible: the first token is still the bare pid)::

    3285778
    host=ywata-note-win
    pid_ns=pid:[4026531836]
    boot_id=3d241760-a072-4468-a853-61a6b6892aae
    container=0
    interval=120.0
    heartbeat=2026-07-13T04:12:00.123456+00:00
"""

from __future__ import annotations

import datetime as _dt
import os
import socket
from pathlib import Path
from typing import Any

#: Default seconds between notifyd ticks (the heartbeat cadence). Lives here —
#: rather than in :mod:`._daemon` — because the READER needs it to judge
#: freshness, and the reader must not import the daemon.
DEFAULT_INTERVAL = 120.0

#: A heartbeat is STALE once it is older than this many tick intervals. 3x
#: tolerates one missed/slow tick plus clock skew across the bind mount without
#: tolerating a daemon that has actually stopped.
STALE_TICKS = 3.0

#: Floor on the staleness threshold, so a very small ``--interval`` cannot make
#: a healthy daemon look dead to a checker that merely ran a moment late.
MIN_STALE_SECONDS = 60.0

_START_HINT = (
    "notifyd is not running — start it: `scitex-cards notifyd` (foreground), "
    "or install the systemd user unit: `scitex-cards notifyd install-unit`"
)

_LEGACY_HINT = (
    "this pidfile carries NO host/namespace stamp (a pre-heartbeat notifyd), so "
    "its pid cannot be interpreted from another PID namespace — if notifyd runs "
    "on the bare host while you are in a container, this verdict may be a FALSE "
    "alarm. Upgrade + restart notifyd so it stamps a heartbeat, then re-check"
)


def _read_first_line(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _pid_namespace() -> str:
    """``/proc/self/ns/pid`` — the inode of the namespace that issues OUR pids."""
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return ""


def _in_container() -> bool:
    """Best-effort container detection (docker/podman/apptainer markers)."""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    return any(
        os.environ.get(var)
        for var in ("APPTAINER_CONTAINER", "SINGULARITY_CONTAINER")
    )


def local_identity() -> dict[str, str]:
    """The identity of the namespace THIS process's pids are meaningful in."""
    return {
        "host": socket.gethostname(),
        "pid_ns": _pid_namespace(),
        "boot_id": _read_first_line("/proc/sys/kernel/random/boot_id"),
        "container": "1" if _in_container() else "0",
    }


def render(
    pid: int,
    *,
    interval: float = DEFAULT_INTERVAL,
    now: _dt.datetime | None = None,
    identity: dict[str, str] | None = None,
) -> str:
    """Render the pidfile body: bare pid on line 1, then ``key=value`` lines."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    ident = identity or local_identity()
    lines = [str(pid)]
    for key in ("host", "pid_ns", "boot_id", "container"):
        lines.append(f"{key}={ident.get(key, '')}")
    lines.append(f"interval={interval}")
    lines.append(f"heartbeat={now.isoformat()}")
    return "\n".join(lines) + "\n"


def parse(text: str) -> dict[str, Any]:
    """Parse a pidfile body. ``pid`` is ``None`` when line 1 is not a number."""
    record: dict[str, Any] = {"pid": None}
    lines = text.strip().splitlines()
    if not lines:
        return record
    try:
        record["pid"] = int(lines[0].split()[0])
    except (ValueError, IndexError):
        record["pid"] = None
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if sep:
            record[key.strip()] = value.strip()
    return record


def writer_is_local(
    record: dict[str, Any], identity: dict[str, str] | None = None
) -> bool | None:
    """Is the recorded pid OURS to interpret? ``None`` = undeterminable.

    True only when the writer's PID NAMESPACE is known and equals ours (and no
    host/boot-id evidence contradicts it). A stamp from a namespace we cannot
    compare against is ``None``, never ``True`` — we refuse to guess, because
    guessing "local" is exactly what produced the false-negative.
    """
    ident = identity or local_identity()
    host_w, host_l = record.get("host"), ident.get("host")
    boot_w, boot_l = record.get("boot_id"), ident.get("boot_id")
    ns_w, ns_l = record.get("pid_ns"), ident.get("pid_ns")

    if host_w and host_l and host_w != host_l:
        return False
    if boot_w and boot_l and boot_w != boot_l:
        return False
    if ns_w and ns_l:
        return ns_w == ns_l
    if not (ns_w or host_w or boot_w):
        return None  # LEGACY pidfile: no identity was ever stamped.
    return None  # Some identity, but no namespace to compare — do not guess.


def stale_after_seconds(record: dict[str, Any]) -> float:
    """The freshness budget for this pidfile's heartbeat, in seconds."""
    try:
        interval = float(record.get("interval") or DEFAULT_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL
    if interval <= 0:
        interval = DEFAULT_INTERVAL
    return max(STALE_TICKS * interval, MIN_STALE_SECONDS)


def heartbeat_age_seconds(
    record: dict[str, Any], now: _dt.datetime | None = None
) -> float | None:
    """Seconds since the last heartbeat, or ``None`` if there is none to read."""
    raw = record.get("heartbeat")
    if not raw:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    # A naive stamp meeting an aware `now` is a TypeError, not a health verdict.
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return (now - stamp).total_seconds()


def _probe_pid(pid: int) -> dict[str, Any]:
    """The precise same-namespace liveness probe — unchanged, still fail-loud."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            "ok": False,
            "state": "dead",
            "detail": f"stale notifyd pidfile: pid {pid} is not running",
            "hint": _START_HINT,
        }
    except PermissionError:
        return {
            "ok": True,
            "state": "alive",
            "detail": f"notifyd alive (pid {pid}, owned by another user)",
            "hint": None,
        }
    except OSError as exc:
        return {
            "ok": True,
            "state": "unknown",
            "detail": f"could not determine notifyd liveness ({exc})",
            "hint": None,
        }
    return {
        "ok": True,
        "state": "alive",
        "detail": f"notifyd alive (pid {pid})",
        "hint": None,
    }


def _by_freshness(
    record: dict[str, Any], now: _dt.datetime | None, where: str
) -> dict[str, Any]:
    """Judge a FOREIGN (or unattributable) pidfile by heartbeat freshness only."""
    age = heartbeat_age_seconds(record, now)
    pid = record.get("pid")
    if age is None:
        # No heartbeat to read AND not our namespace: we genuinely cannot tell.
        return {
            "ok": True,
            "state": "unknown",
            "detail": (
                f"notifyd pidfile was written {where} and carries no heartbeat — "
                "cannot determine liveness from here"
            ),
            "hint": None,
        }
    budget = stale_after_seconds(record)
    if age <= budget:
        return {
            "ok": True,
            "state": "alive",
            "detail": (
                f"notifyd alive (pid {pid} {where}; heartbeat {age:.0f}s old, "
                f"within {budget:.0f}s) — pid NOT probed: it is meaningless in "
                "this namespace"
            ),
            "hint": None,
        }
    return {
        "ok": False,
        "state": "dead",
        "detail": (
            f"notifyd heartbeat is STALE: last tick {age:.0f}s ago (budget "
            f"{budget:.0f}s); pidfile written {where}"
        ),
        "hint": (
            "notifyd stopped ticking — on the host that runs it: "
            "`systemctl --user status scitex-cards-notifyd` and "
            "`systemctl --user restart scitex-cards-notifyd` "
            "(or run `scitex-cards notifyd` in the foreground)"
        ),
    }


def assess_liveness(
    pidfile: Path,
    *,
    now: _dt.datetime | None = None,
    identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The namespace-agnostic notifyd liveness verdict.

    Returns ``{ok, state, detail, hint}`` where ``state`` is one of
    ``not_running`` / ``alive`` / ``dead`` / ``unknown``. NEVER concludes death
    from a pid it cannot interpret.
    """
    if not pidfile.exists():
        return {
            "ok": False,
            "state": "not_running",
            "detail": "no notifyd pidfile",
            "hint": _START_HINT,
        }
    try:
        text = pidfile.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": True,
            "state": "unknown",
            "detail": f"notifyd pidfile {pidfile} is unreadable ({exc})",
            "hint": None,
        }

    record = parse(text)
    ident = identity or local_identity()
    local = writer_is_local(record, ident)
    pid = record.get("pid")

    if local is True and pid is not None:
        return _probe_pid(pid)

    if pid is None:
        # Unparseable pid — freshness is all we have (and may be nothing).
        return _by_freshness(record, now, "with an unparseable pid")

    if local is False:
        host = record.get("host") or "?"
        ns = record.get("pid_ns") or "?"
        return _by_freshness(record, now, f"elsewhere (host={host} pid_ns={ns})")

    # local is None — the writer left no namespace stamp we can compare.
    if record.get("heartbeat"):
        return _by_freshness(record, now, "by an unidentified namespace")

    # LEGACY pidfile (bare pid, no stamp, no heartbeat). The pid probe is the
    # only signal in existence, so we still use it and still FAIL LOUD on a
    # lookup miss — but we say plainly that the verdict may be a cross-namespace
    # artefact, and how to make it trustworthy.
    verdict = _probe_pid(pid)
    if not verdict["ok"]:
        verdict["detail"] += " (pidfile has no host/namespace stamp)"
        verdict["hint"] = f"{_LEGACY_HINT}. Otherwise: {_START_HINT}"
    return verdict


__all__ = [
    "DEFAULT_INTERVAL",
    "MIN_STALE_SECONDS",
    "STALE_TICKS",
    "assess_liveness",
    "heartbeat_age_seconds",
    "local_identity",
    "parse",
    "render",
    "stale_after_seconds",
    "writer_is_local",
]

# EOF
