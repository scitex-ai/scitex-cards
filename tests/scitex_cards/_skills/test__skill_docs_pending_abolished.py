#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the skill-docs pending-abolition sweep.

The bundled skill docs (``src/scitex_cards/_skills/scitex-cards/*.md``)
taught consuming agents to use ``--status pending`` /
``status="pending"`` / ``update --status pending`` (reopen) AFTER
``pending`` was abolished 2026-07-10. ``--status`` is a closed
``click.Choice`` over ``VALID_STATUSES``, so those copy-pasteable
examples do not fail silently — the CLI rejects them outright — and
the reopen recipe pointed at a status the validator refuses. Same
dead-instruction class as the CLI-help sweep
(``test__cli_help_pending_abolished``) and the PreToolUse hook fix
(the hook script already suggests ``--status deferred``).

The ONE exemption is ``40_task-harvest.md``: its ``status: pending``
lines sit inside a dated (2026-06-08, pre-abolition) worked example —
a historical record, not an instruction. The exemption is pinned to
that single file so a new live-status ``pending`` anywhere else fails.
The exemption does NOT cover the live Phase-1 unblock procedure in
the same file: ``flip … to `pending``` is forbidden everywhere with
no exemption (its only occurrence was the harvest unblock step, now
``to `deferred```).

These pins read the bundled ``.md`` files and render ``--help``
through ``CliRunner`` only: no store, no PostgreSQL — they run in
containers where the store-backed suite refuses via the fail-LOUD
guard.
"""

from __future__ import annotations

import re

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

#: Copy-pasteable spellings of the abolished status that must not appear
#: in any bundled skill doc. Plain ``pending`` is NOT on this list:
#: abolition notes ("``pending`` was abolished 2026-07-10"), the
#: ``user-pending`` blocker family, and delivery-domain prose ("pending
#: notifications") are legitimate.
_FORBIDDEN_RES = (
    re.compile(r"--status\s+pending\b"),
    re.compile(r"""status\s*=\s*["']pending["']"""),
)

#: ``status: pending`` is additionally forbidden everywhere EXCEPT the
#: dated worked example in 40 (pinned separately below).
_STATUS_COLON_RE = re.compile(r"status:\s*pending\b")

#: A live unblock/flip instruction naming the abolished status
#: (``flip the blocked task to `pending```). Forbidden in EVERY skill
#: doc with no exemption: the dated worked example in 40 never used
#: this spelling, so any occurrence is a live dead-instruction.
_FLIP_TO_PENDING_RE = re.compile(r"to\s+`pending`")

#: The only file allowed to name a ``status: pending`` row: the dated
#: pre-abolition worked example.
_HISTORICAL_EXEMPTION = "40_task-harvest.md"


def _skill_docs() -> dict[str, str]:
    root = _skills_root()
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.md"))
    }


def test_no_forbidden_pending_spelling_in_any_skill_doc():
    docs = _skill_docs()
    assert docs, "no bundled skill docs found — resolver broken?"
    for name, text in docs.items():
        for rx in _FORBIDDEN_RES:
            assert rx.search(text) is None, f"{name}: {rx.pattern!r} still present"


def test_status_colon_pending_only_in_the_dated_worked_example():
    docs = _skill_docs()
    holders = [n for n, t in docs.items() if _STATUS_COLON_RE.search(t)]
    assert holders == [_HISTORICAL_EXEMPTION], holders
    # And the exemption really is the dated pre-abolition record, not a
    # live instruction that drifted.
    assert "2026-06-08" in docs[_HISTORICAL_EXEMPTION]


def test_detector_is_not_vacuous():
    fixture = (
        "scitex-cards list-tasks --assignee <you> --status pending\n"
        '_store.update_task(p, id, status="pending")\n'
        "- 135 `status: pending` (= RUNNABLE)\n"
        "flip the blocked task\n   to `pending` + clear `blocker`\n"
    )
    assert any(rx.search(fixture) for rx in _FORBIDDEN_RES)
    assert _STATUS_COLON_RE.search(fixture)
    assert _FLIP_TO_PENDING_RE.search(fixture)


def test_no_live_flip_to_pending_instruction_in_any_skill_doc():
    docs = _skill_docs()
    holders = [n for n, t in docs.items() if _FLIP_TO_PENDING_RE.search(t)]
    assert holders == [], holders


def test_harvest_unblock_names_the_successor_state():
    docs = _skill_docs()
    harvest = docs["40_task-harvest.md"]
    # The live Phase-1 procedure sits BEFORE the dated worked example —
    # pin the successor there so the exemption section cannot satisfy it.
    # Whitespace is normalized: the abolition note wraps across lines.
    live_procedure = harvest.split("## Worked example")[0]
    assert "to `deferred`" in live_procedure
    flat = " ".join(live_procedure.split())
    assert "`pending` was abolished 2026-07-10" in flat


def test_harvest_worked_example_flags_the_abolished_status():
    docs = _skill_docs()
    example = docs["40_task-harvest.md"].split("## Worked example")[1]
    flat = " ".join(example.split())
    # A reader landing mid-example must learn, at the point of use, that
    # the `pending` rows are a pre-abolition record — not copy-pasteable.
    assert "do NOT copy" in example
    assert "`deferred` today" in example
    assert "2026-06-08" in flat


def test_redirect_copy_suggests_the_successor_default():
    docs = _skill_docs()
    assert "--status deferred" in docs["22_pretooluse-hook-redirect.md"]


def test_reopen_recipe_names_the_canonical_transition():
    docs = _skill_docs()
    reopen_section = docs["43_consuming-agent-schema-and-crud.md"].split("### RE-OPEN")[1]
    assert "--status blocked" in reopen_section
    assert "--blocker operator-decision" in reopen_section
    assert "reopen_task" in reopen_section


def test_schema_enum_mirrors_valid_statuses():
    docs = _skill_docs()
    for name in ("03_python-api.md", "43_consuming-agent-schema-and-crud.md"):
        row = _enum_row(docs[name])
        assert "`pending`" not in row, name
        assert "`cancelled`" in row, name
    # The abolition note lives as prose beside the row (not inside it),
    # so an agent meeting a legacy row learns the successors.
    for name in ("03_python-api.md", "43_consuming-agent-schema-and-crud.md"):
        assert "`pending` was abolished 2026-07-10" in docs[name], name


def _enum_row(text: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            continue
        if "VALID_STATUSES" in line or stripped.startswith("| `status`"):
            return line
    raise AssertionError("no status enum row found")


def test_consumption_loop_predicate_names_the_backlog_state():
    docs = _skill_docs()
    loop = docs["32_agent-self-consumption-loop.md"]
    assert "{deferred, in_progress}" in loop
    assert "{pending, in_progress}" not in loop


def test_cli_status_choice_rejects_pending():
    for verb in ("add", "update"):
        out = CliRunner().invoke(main, [verb, "--help"])
        assert out.exit_code == 0, out.output
        assert "pending" not in _choice_line(out.output, "--status"), verb


def _choice_line(help_text: str, flag: str) -> str:
    for line in help_text.splitlines():
        if flag in line:
            return line
    raise AssertionError(f"{flag} not in help")


def test_add_defaults_to_the_successor_state():
    out = CliRunner().invoke(main, ["add", "--help"])
    assert out.exit_code == 0, out.output
    assert "[default: deferred]" in out.output


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
