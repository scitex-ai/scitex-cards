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


def test_skill_docs_resolve_for_forbidden_spelling_scan():
    # Arrange
    # Act
    docs = _skill_docs()
    # Assert
    assert docs, "no bundled skill docs found — resolver broken?"


def test_no_forbidden_pending_spelling_in_any_skill_doc():
    # Arrange
    # Act
    docs = _skill_docs()
    bad = [
        (name, rx.pattern)
        for name, text in docs.items()
        for rx in _FORBIDDEN_RES
        if rx.search(text) is not None
    ]
    # Assert
    assert not bad, bad


def test_status_colon_pending_only_in_the_dated_worked_example_case1():
    # Arrange
    docs = _skill_docs()
    # Act
    holders = [n for n, t in docs.items() if _STATUS_COLON_RE.search(t)]
    # Assert
    assert holders == [_HISTORICAL_EXEMPTION], holders


def test_status_colon_pending_only_in_the_dated_worked_example_case2():
    # Arrange
    # Act
    docs = _skill_docs()
    # Assert
    holders = [n for n, t in docs.items() if _STATUS_COLON_RE.search(t)]
    assert "2026-06-08" in docs[_HISTORICAL_EXEMPTION]


def test_detector_is_not_vacuous_case1():
    # Arrange
    # Act
    fixture = (
        "scitex-cards list-tasks --assignee <you> --status pending\n"
        '_store.update_task(p, id, status="pending")\n'
        "- 135 `status: pending` (= RUNNABLE)\n"
        "flip the blocked task\n   to `pending` + clear `blocker`\n"
    )
    # Assert
    assert any(rx.search(fixture) for rx in _FORBIDDEN_RES)


def test_detector_is_not_vacuous_case2():
    # Arrange
    # Act
    fixture = (
        "scitex-cards list-tasks --assignee <you> --status pending\n"
        '_store.update_task(p, id, status="pending")\n'
        "- 135 `status: pending` (= RUNNABLE)\n"
        "flip the blocked task\n   to `pending` + clear `blocker`\n"
    )
    # Assert
    assert _STATUS_COLON_RE.search(fixture)


def test_detector_is_not_vacuous_case3():
    # Arrange
    # Act
    fixture = (
        "scitex-cards list-tasks --assignee <you> --status pending\n"
        '_store.update_task(p, id, status="pending")\n'
        "- 135 `status: pending` (= RUNNABLE)\n"
        "flip the blocked task\n   to `pending` + clear `blocker`\n"
    )
    # Assert
    assert _FLIP_TO_PENDING_RE.search(fixture)


def test_no_live_flip_to_pending_instruction_in_any_skill_doc():
    # Arrange
    docs = _skill_docs()
    # Act
    holders = [n for n, t in docs.items() if _FLIP_TO_PENDING_RE.search(t)]
    # Assert
    assert holders == [], holders


def test_harvest_unblock_names_the_successor_state_case1():
    # Arrange
    docs = _skill_docs()
    harvest = docs["40_task-harvest.md"]
    # Act
    live_procedure = harvest.split("## Worked example")[0]
    # Assert
    flat = " ".join(live_procedure.split())
    assert "to `deferred`" in live_procedure


def test_harvest_unblock_names_the_successor_state_case2():
    # Arrange
    docs = _skill_docs()
    harvest = docs["40_task-harvest.md"]
    live_procedure = harvest.split("## Worked example")[0]
    # Act
    flat = " ".join(live_procedure.split())
    # Assert
    assert "`pending` was abolished 2026-07-10" in flat


def test_harvest_worked_example_flags_the_abolished_status_case1():
    # Arrange
    docs = _skill_docs()
    # Act
    example = docs["40_task-harvest.md"].split("## Worked example")[1]
    # Assert
    flat = " ".join(example.split())
    assert "do NOT copy" in example


def test_harvest_worked_example_flags_the_abolished_status_case2():
    # Arrange
    docs = _skill_docs()
    # Act
    example = docs["40_task-harvest.md"].split("## Worked example")[1]
    # Assert
    flat = " ".join(example.split())
    assert "`deferred` today" in example


def test_harvest_worked_example_flags_the_abolished_status_case3():
    # Arrange
    docs = _skill_docs()
    example = docs["40_task-harvest.md"].split("## Worked example")[1]
    # Act
    flat = " ".join(example.split())
    # Assert
    assert "2026-06-08" in flat


def test_redirect_copy_suggests_the_successor_default():
    # Arrange
    # Act
    docs = _skill_docs()
    # Assert
    assert "--status deferred" in docs["22_pretooluse-hook-redirect.md"]


def test_reopen_recipe_names_the_canonical_transition_case1():
    # Arrange
    docs = _skill_docs()
    # Act
    reopen_section = docs["43_consuming-agent-schema-and-crud.md"].split("### RE-OPEN")[1]
    # Assert
    assert "--status blocked" in reopen_section


def test_reopen_recipe_names_the_canonical_transition_case2():
    # Arrange
    docs = _skill_docs()
    # Act
    reopen_section = docs["43_consuming-agent-schema-and-crud.md"].split("### RE-OPEN")[1]
    # Assert
    assert "--blocker operator-decision" in reopen_section


def test_reopen_recipe_names_the_canonical_transition_case3():
    # Arrange
    docs = _skill_docs()
    # Act
    reopen_section = docs["43_consuming-agent-schema-and-crud.md"].split("### RE-OPEN")[1]
    # Assert
    assert "reopen_task" in reopen_section


_ENUM_ROW_DOCS = ("03_python-api.md", "43_consuming-agent-schema-and-crud.md")


def test_schema_enum_row_omits_pending():
    # Arrange
    docs = _skill_docs()
    # Act
    rows = {name: _enum_row(docs[name]) for name in _ENUM_ROW_DOCS}
    bad = [name for name, row in rows.items() if "`pending`" in row]
    # Assert
    assert not bad, bad


def test_schema_enum_row_keeps_cancelled():
    # Arrange
    docs = _skill_docs()
    # Act
    rows = {name: _enum_row(docs[name]) for name in _ENUM_ROW_DOCS}
    bad = [name for name, row in rows.items() if "`cancelled`" not in row]
    # Assert
    assert not bad, bad


def test_schema_enum_abolition_note_lives_beside_row():
    # The abolition note lives as prose beside the row (not inside it),
    # so an agent meeting a legacy row learns the successors.
    # Arrange
    docs = _skill_docs()
    # Act
    bad = [
        name
        for name in _ENUM_ROW_DOCS
        if "`pending` was abolished 2026-07-10" not in docs[name]
    ]
    # Assert
    assert not bad, bad


def _enum_row(text: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            continue
        if "VALID_STATUSES" in line or stripped.startswith("| `status`"):
            return line
    raise AssertionError("no status enum row found")


def test_consumption_loop_predicate_names_the_backlog_state_case1():
    # Arrange
    docs = _skill_docs()
    # Act
    loop = docs["32_agent-self-consumption-loop.md"]
    # Assert
    assert "{deferred, in_progress}" in loop


def test_consumption_loop_predicate_names_the_backlog_state_case2():
    # Arrange
    docs = _skill_docs()
    # Act
    loop = docs["32_agent-self-consumption-loop.md"]
    # Assert
    assert "{pending, in_progress}" not in loop


def test_cli_status_help_exits_zero_for_add_and_update():
    # Arrange
    verbs = ("add", "update")
    # Act
    bad = [
        verb
        for verb in verbs
        if CliRunner().invoke(main, [verb, "--help"]).exit_code != 0
    ]
    # Assert
    assert not bad, bad


def test_cli_status_choice_rejects_pending():
    # Arrange
    verbs = ("add", "update")
    # Act
    outs = {verb: CliRunner().invoke(main, [verb, "--help"]) for verb in verbs}
    bad = [
        verb
        for verb, out in outs.items()
        if "pending" in _choice_line(out.output, "--status")
    ]
    # Assert
    assert not bad, bad


def _choice_line(help_text: str, flag: str) -> str:
    for line in help_text.splitlines():
        if flag in line:
            return line
    raise AssertionError(f"{flag} not in help")


def test_add_defaults_to_the_successor_state_case1():
    # Arrange
    # Act
    out = CliRunner().invoke(main, ["add", "--help"])
    # Assert
    assert out.exit_code == 0, out.output


def test_add_defaults_to_the_successor_state_case2():
    # Arrange
    # Act
    out = CliRunner().invoke(main, ["add", "--help"])
    # Assert
    assert "[default: deferred]" in out.output


def test_pending_stays_abolished_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_stays_abolished_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_pending_stays_abolished_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
