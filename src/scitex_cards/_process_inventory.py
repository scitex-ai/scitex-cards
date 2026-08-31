#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enumerate the processes that import this package — NOT the venvs that hold it.

WHY THIS EXISTS
---------------
Constitution §2: "A long-lived process holds the world as it was at boot ...
Merged is not live; fresh on disk is not fresh in memory. So when a fix must
take effect, NAME THE PROCESSES THAT MUST RESTART, and verify the restart
happened."

Naming them by hand has failed eight times, each time by asking a NARROWER
question than the situation contained:

    one named unit          -> `SubState=dead` for a unit that does not exist is
                               indistinguishable from one that exists and is stopped
    the unit list           -> a daemon under nohup/tmux/cron is in no unit list
    the process list        -> taken from inside a container, it cannot see host daemons
    the image               -> verified 0.49.1 in the SIF while every running
                               process held 0.49.0
    a start time            -> used as a proxy for a version
    THE INSTALLED PACKAGE   -> an editable checkout on sys.path shadows the wheel,
                               so the venv's version is not the process's version

THE LAST ONE IS WHY THIS MODULE SEPARATES TWO FIELDS THAT EVERY EARLIER ATTEMPT
CONFLATED. ``venv_version`` is what the venv on disk holds NOW. ``resolved_*``
is where the package actually resolves FOR THAT PROCESS. They disagree whenever
a process was launched from a source checkout, and when they disagree the venv
answer is not merely unavailable — it is CONFIDENTLY WRONG, which no
"unreadable" sentinel would have caught.

WHAT THIS DELIBERATELY REFUSES TO DO
------------------------------------
It never guesses a version it cannot read. A containerised process's site
-packages is behind a user namespace (``/proc/<pid>/root`` is EPERM even for
the owning user), and its image may be deleted-but-open, so the payload is
unreadable from any vantage. Such a row reports ``VERSION-UNREADABLE`` and is
STILL LISTED. An inventory that silently drops what it cannot read produces a
clean-looking report of the processes it happened to be able to see — this
card's own "236 units listed, zero match is evidence; dead is not", one level
down.

It also reports :attr:`Inventory.enumerated`, the size of the population
scanned, because a scan returning no rows and a scan that could not run look
identical otherwise.

AND IT EXCLUDES ITSELF
----------------------
A previous hand-rolled version of this scan matched its own shell: the probe
text sat in bash's ``cmdline``, so the instrument appeared in the population it
was measuring. :func:`scan` therefore drops its own pid and its parent by
default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Reported instead of a version when the answer is genuinely unreadable —
#: never omitted, never guessed. See the module docstring.
UNREADABLE = "VERSION-UNREADABLE"

#: Reported when the process's own import path cannot be determined from this
#: vantage. Distinct from UNREADABLE: the version might be perfectly readable
#: on disk, but WHICH file this process imports is not knowable from outside.
UNRESOLVED = "IMPORT-PATH-UNKNOWN"

#: A process that re-execs per call is never stale: the next invocation picks up
#: whatever the venv holds. Conflating it with a daemon is how "restart it" got
#: recommended for things that never needed one.
PER_INVOCATION = "per-invocation"
LONG_LIVED = "long-lived"

#: Substrings in a cmdline that mark a process as importing this package.
_MARKERS = ("scitex_cards", "scitex-cards")

#: Long-lived entry points. Anything else matching a marker is assumed to be a
#: one-shot CLI call, which is the safe default: calling a daemon
#: per-invocation would understate staleness, and this errs the other way.
_DAEMON_TOKENS = ("notifyd", "serve", "gui", "mcp", "board", "watch", "hub")


@dataclass(frozen=True)
class ProcessRow:
    """One process that imports this package, and what can be said about it."""

    pid: int
    cmdline: str
    start_time: str
    lifetime_class: str
    venv_path: str
    venv_version: str
    resolved_import_path: str
    resolved_version: str

    @property
    def staleness(self) -> str:
        """Version gap, or why there isn't one.

        ``N/A`` for a per-invocation process: it cannot be stale. ``UNKNOWN``
        whenever either side is unreadable — which is most containerised rows,
        and saying so is the point.
        """
        if self.lifetime_class == PER_INVOCATION:
            return "N/A"
        if UNREADABLE in (self.venv_version, self.resolved_version):
            return "UNKNOWN"
        if self.resolved_version == UNRESOLVED:
            return "UNKNOWN"
        if self.resolved_version == self.venv_version:
            return "current"
        return f"{self.resolved_version} running, {self.venv_version} on disk"


@dataclass(frozen=True)
class Inventory:
    """The rows, plus the two facts that make a zero interpretable."""

    rows: list[ProcessRow] = field(default_factory=list)
    #: How many processes were examined. A zero-row result against a zero-sized
    #: enumeration means the scan could not run; against a large one it is
    #: evidence.
    enumerated: int = 0
    #: ``container`` or ``host``. An empty result means something different
    #: from each, so the report must say which one produced it.
    vantage: str = "unknown"


def detect_vantage(proc_root: Path) -> str:
    """Whether this scan can see host processes at all.

    A container's ``/proc`` shows only its own namespace, so an empty result
    there means WRONG VANTAGE, not "not running" — the distinction that cost a
    wrong fleet-wide conclusion on 2026-08-21.
    """
    return "container" if (proc_root / "1" / "root" / ".dockerenv").exists() or Path(
        "/.dockerenv"
    ).exists() or os.environ.get("SAC_NAME") else "host"


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except (OSError, PermissionError):
        return ""


def _classify(cmdline: str) -> str:
    return LONG_LIVED if any(t in cmdline for t in _DAEMON_TOKENS) else PER_INVOCATION


def _venv_of(exe: str) -> str:
    """The venv root for an executable, or '' if it is not in one."""
    if not exe:
        return ""
    for parent in Path(exe).parents:
        if (parent / "pyvenv.cfg").exists():
            return str(parent)
    return ""


def _interpreter_candidates(cmdline: str, exe: str) -> tuple[str, ...]:
    """Paths that might sit inside a venv, most-specific first.

    ``realpath(/proc/<pid>/exe)`` IS NOT THE VENV. A venv's ``bin/python`` is a
    symlink to the system interpreter, and realpath follows it all the way out
    — so a process launched from ``/opt/venv-sac/bin/python`` reports
    ``/usr/bin/python3.12``, whose parents contain no ``pyvenv.cfg``. The venv
    is then reported UNREADABLE when it was sitting in argv[0] the whole time.

    Measured on this module's own first live run: three real processes, all
    three venvs lost. The same failure as every instance on the card that
    prompted this module — resolving to a NEARBY object and reporting about
    that instead. argv[0] is checked FIRST because it preserves the path as
    invoked, which is the thing that actually determines sys.prefix.
    """
    argv0 = cmdline.split(" ", 1)[0] if cmdline else ""
    return tuple(c for c in (argv0, exe) if c.startswith("/"))


def _venv_version(venv: str) -> str:
    """The version the venv holds ON DISK NOW — NOT what any process imports."""
    if not venv:
        return UNREADABLE
    roots = list(Path(venv).glob("lib/python*/site-packages"))
    for root in roots:
        for dist in root.glob("scitex_cards-*.dist-info"):
            name = dist.name[len("scitex_cards-") : -len(".dist-info")]
            return name
    return UNREADABLE


def scan(
    proc_root: Path | None = None,
    self_pid: int | None = None,
    parent_pid: int | None = None,
) -> Inventory:
    """Enumerate processes importing this package.

    ``proc_root`` is injectable so this can be exercised against a real
    directory tree rather than a mocked filesystem (PA-306: no mocks). The
    defaults exclude this process and its parent, so the instrument does not
    appear in the population it measures.
    """
    proc_root = Path("/proc") if proc_root is None else Path(proc_root)
    self_pid = os.getpid() if self_pid is None else self_pid
    parent_pid = os.getppid() if parent_pid is None else parent_pid
    skip = {self_pid, parent_pid}

    rows: list[ProcessRow] = []
    examined = 0
    if not proc_root.is_dir():
        return Inventory(rows=[], enumerated=0, vantage=detect_vantage(proc_root))

    for entry in sorted(proc_root.iterdir()):
        if not entry.name.isdigit():
            continue
        examined += 1
        pid = int(entry.name)
        if pid in skip:
            continue
        cmdline = _read(entry / "cmdline").replace("\x00", " ").strip()
        if not cmdline or not any(m in cmdline for m in _MARKERS):
            continue
        exe = os.path.realpath(entry / "exe") if (entry / "exe").exists() else ""
        venv = ""
        for candidate in _interpreter_candidates(cmdline, exe):
            venv = _venv_of(candidate)
            if venv:
                break
        rows.append(
            ProcessRow(
                pid=pid,
                cmdline=cmdline[:160],
                start_time=_stat_start(entry),
                lifetime_class=_classify(cmdline),
                venv_path=venv or UNREADABLE,
                venv_version=_venv_version(venv),
                # NOT KNOWABLE FROM OUTSIDE. Only the process itself can say
                # which file its `scitex_cards` resolved to; an editable
                # checkout on its sys.path is invisible here. Reporting the
                # venv's answer in this field is precisely the error this
                # module was built after.
                resolved_import_path=UNRESOLVED,
                resolved_version=UNRESOLVED,
            )
        )
    return Inventory(rows=rows, enumerated=examined, vantage=detect_vantage(proc_root))


def _stat_start(entry: Path) -> str:
    """Process start time as an mtime-derived ISO stamp, or '' if unreadable."""
    try:
        import datetime

        ts = entry.stat().st_mtime
        return (
            datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return ""


def describe_self() -> dict:
    """What THIS interpreter actually imports — the one row always knowable.

    Every other row in the inventory reports ``IMPORT-PATH-UNKNOWN`` for the
    resolved fields, because a process's own sys.path is not readable from
    outside. Running this inside a process is the only way to fill them, which
    is why the deployment doctrine is "ask each process", not "read the venv".
    """
    import scitex_cards

    return {
        "pid": os.getpid(),
        "resolved_import_path": getattr(scitex_cards, "__file__", UNRESOLVED),
        "resolved_version": getattr(scitex_cards, "__version__", UNREADABLE),
    }


# EOF
