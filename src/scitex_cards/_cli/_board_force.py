#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One ``--force`` takeover, shared by every verb that starts a board.

OPERATOR REQUEST, 2026-08-14 (Telegram): 「--force option 入れておいてください。
stop するのめんどくさいので。あとはなければ通すように。stop ではないので。」 --
he ran ``scitex-cards gui serve --force`` and got "No such option '--force'".
He wants one command that serves whether or not a board is already up, because
typing ``gui stop`` first is busywork he has to remember every single time.

「なければ通すように。stop ではないので」 IS THE HALF THAT IS EASY TO GET WRONG.
``--force`` is not a stop verb wearing a different hat. Nothing running is the
NORMAL case, not an error case: a takeover with no incumbent is simply a serve.
A ``--force`` that complained "no board to stop" would be exactly the busywork
he asked to delete, one message later.

WHY THIS IS A MODULE AND NOT A LINE IN EACH COMMAND. ``gui serve`` and
``board start`` are two doors onto ONE Django app, and this repo has already
paid for treating them as two: the unconfigured-store refusal was written for
``gui serve`` alone on 2026-08-09 and ``board start`` stayed unguarded for
three days, so whether the operator was protected depended on which verb he
happened to type (see :mod:`._store_guard`, which exists for the same reason).
A takeover implemented once per door is a takeover that will be missing --
or subtly different -- at the next door somebody adds.

NEVER KILL BY PORT. Resolution goes through :func:`._board_proc._board_resolve_pid`
and nothing else, because its ``_board_cmdline_is_board`` marker guard is the
only thing standing between "take over my board" and "SIGTERM whoever happens
to hold 8051".
"""

from __future__ import annotations

import click

from ._board_proc import StopOutcome, _board_resolve_pid, stop_board_process

__all__ = ["announce_takeover", "force_stop_running_board"]

#: Same graceful-exit budget ``board stop`` defaults to. One number, so a
#: takeover never escalates on a different schedule than the stop verb.
FORCE_STOP_TIMEOUT = 5.0


def force_stop_running_board(
    port: int,
    *,
    timeout: float = FORCE_STOP_TIMEOUT,
    dry_run: bool = False,
) -> int | None:
    """Clear the way for a fresh board on ``port``. Returns the pid, or None.

    ``None`` means nothing was running -- the operator's 「なければ通すように」
    case, and a completely ordinary success. The caller serves either way.

    Resolution uses the pidfile FIRST and the (cmdline-verified) port second,
    so a stale pidfile with a live board still gets taken over. That second
    path is why this cannot be ``_board_read_pid``: the stale-pidfile incident
    is precisely the state in which somebody reaches for ``--force``.

    ``dry_run`` prints what WOULD happen and signals nothing. It names the pid,
    because "would stop the board" is not an answer to "which process are you
    about to kill" -- and a dry run is read by someone who wants that answer
    before committing.

    RAISES ``click.ClickException`` when the stop is REFUSED by the kernel
    (a cross-user kill, most often). The caller must NOT go on to bind a port
    that is demonstrably still held: the bind would fail later, further from
    the cause, or -- worse -- succeed on a different interface and leave two
    boards up. Failing here names the pid AND the next step.

    A SIGKILL that we could not re-verify is NOT a refusal (``stopped is
    None``). SIGKILL is uncatchable, so proceeding is right; we say so loudly
    on stderr rather than pretending we watched it exit.
    """
    pid, untracked = _board_resolve_pid(port)
    if pid is None:
        if dry_run:
            click.echo("# dry-run: --force: no board is running; would just serve.")
        return None

    note = " (untracked pidfile; found on port)" if untracked else ""
    if dry_run:
        click.echo(
            f"# dry-run: --force would stop pid {pid}{note} "
            f"(SIGTERM, timeout {timeout}s, then SIGKILL) before serving.",
        )
        return pid

    click.echo(f"# --force: stopping the running board (pid {pid}){note}.")
    announce_takeover(stop_board_process(pid, timeout), port)
    return pid


def announce_takeover(outcome: StopOutcome, port: int) -> None:
    """Turn a :class:`StopOutcome` into operator-facing words, or refuse.

    SPLIT OUT OF THE CALLER SO THE REFUSAL IS TESTABLE AT ALL. The kernel
    only refuses a signal for reasons a test cannot manufacture in-process
    (a cross-user kill needs a second user; the resolve-then-vanish race
    needs a scheduler hook), and ``_board_resolve_pid`` filters out every
    pid that ``kill(pid, 0)`` rejects — so no CLI invocation this suite can
    write reaches the refusal branch. Taking a REAL ``StopOutcome`` (one
    that :func:`stop_board_process` built from a REAL ``ESRCH``) is how the
    branch gets covered without mocking anything.

    All three of :attr:`StopOutcome.stopped`'s values get their own branch,
    which is the point of it being three-valued.
    """
    if outcome.stopped is False:
        raise click.ClickException(
            f"{outcome.error}. The board (pid {outcome.pid}) may still be "
            f"running and still holding port {port}, so --force will NOT serve "
            f"on top of it. Next: check who owns it with "
            f"`ps -o user=,cmd= -p {outcome.pid}` — the kernel denies a "
            f"cross-user kill, so stop it as that user, or serve elsewhere "
            f"with --port <other>."
        )
    if outcome.stopped is None:
        # SIGKILL sent, exit UNOBSERVED. Say exactly that; do not upgrade an
        # unknown to "stopped" just because SIGKILL is usually enough.
        click.echo(
            f"# board did not exit in {outcome.timeout}s; sent SIGKILL to pid "
            f"{outcome.pid} (exit not re-checked).",
            err=True,
        )
        return
    click.echo(f"# stopped board (pid {outcome.pid}).")


# EOF
