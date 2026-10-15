#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PS-220 stdout byte contract for the git-hook content transport.

WHY THIS FILE EXISTS, from a measurement rather than a style preference.
``scitex_cards._output.write_content`` replaced two bare ``print`` calls
(``_git_link.py`` card-id line + push-event JSON, ``_output.py`` itself) to
satisfy PS-220 under scitex-dev >= 0.62, which has no configuration or
comment bypass — including for JSON and protocol payloads. The prescribed
transport is ``scitex_logging.getPlainConsole.emit``, and the migration is
only safe if the bytes on stdout are unchanged: the shell hook does
``card_id=$(… card-id <branch> <msg>)`` and treats the captured bytes as
the card id, so a level prefix, a stderr redirect, or a missing newline
breaks the consumer twice over.

Card ``cards-audit-4-develop-inherited-errors-ps220-ps234-20261012`` owns
the repair; these tests pin the contract so a future transport change
fails here instead of in a hook at commit time.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. ``capsys`` is the pytest-owned
stream capture fixture (precedent: test__tolerated_warning_names_its_side),
not a mock. No store is touched — every test is pure.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from scitex_cards import _git_link as _git_link_module
from scitex_cards import _output as _output_module
from scitex_cards._git_link import _main
from scitex_cards._output import write_content


def _bare_print_call_count(source: str) -> int:
    """Count bare ``print(...)`` calls (AST — docstring mentions ignored)."""
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(getattr(node, "func", None), "id", "") == "print"
    )


def test_write_content_emits_payload_plus_newline_to_stdout(capsys):
    # Arrange
    payload = "my-card-123-fix"
    # Act
    write_content(payload)
    # Assert
    assert capsys.readouterr().out == payload + "\n"


def test_write_content_emits_nothing_to_stderr(capsys):
    # Arrange
    payload = "my-card-123-fix"
    # Act
    write_content(payload)
    # Assert
    assert capsys.readouterr().err == ""


def test_write_content_preserves_trailing_whitespace_verbatim(capsys):
    # Arrange
    payload = "my-card-123-fix-with-trailing-space  "
    # Act
    write_content(payload)
    # Assert
    assert capsys.readouterr().out == payload + "\n"


def test_write_content_preserves_unicode_verbatim(capsys):
    # Arrange
    payload = "titre-és-日本語"
    # Act
    write_content(payload)
    # Assert
    assert capsys.readouterr().out == payload + "\n"


def test_git_link_card_id_emits_bare_id_to_stdout(capsys):
    # Arrange
    argv = ["card-id", "feat/my-card-123-fix"]
    # Act
    rc = _main(argv)
    # Assert
    assert capsys.readouterr().out == "my-card-123-fix\n"


def test_git_link_card_id_returns_zero(capsys):
    # Arrange
    argv = ["card-id", "feat/my-card-123-fix"]
    # Act
    rc = _main(argv)
    # Assert
    assert rc == 0


def test_git_link_card_id_emits_nothing_to_stderr(capsys):
    # Arrange
    argv = ["card-id", "feat/my-card-123-fix"]
    # Act
    _main(argv)
    # Assert
    assert capsys.readouterr().err == ""


def test_git_link_card_id_adhoc_branch_emits_nothing(capsys):
    # Arrange
    argv = ["card-id", "develop"]
    # Act
    _main(argv)
    # Assert
    assert capsys.readouterr().out == ""


def test_git_link_emit_event_stdout_is_single_line_json(capsys):
    # Arrange
    argv = [
        "emit-event",
        "--repo",
        "scitex-cards",
        "--branch",
        "feat/my-card-123-fix",
        "--sha",
        "abc123",
    ]
    # Act
    _main(argv)
    # Assert
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_git_link_emit_event_json_carries_the_card_id(capsys):
    # Arrange
    argv = [
        "emit-event",
        "--repo",
        "scitex-cards",
        "--branch",
        "feat/my-card-123-fix",
        "--sha",
        "abc123",
    ]
    # Act
    _main(argv)
    # Assert
    assert json.loads(capsys.readouterr().out)["card_ids"] == ["my-card-123-fix"]


def test_git_link_emit_event_emits_nothing_to_stderr(capsys):
    # Arrange
    argv = [
        "emit-event",
        "--repo",
        "scitex-cards",
        "--branch",
        "feat/my-card-123-fix",
        "--sha",
        "abc123",
    ]
    # Act
    _main(argv)
    # Assert
    assert capsys.readouterr().err == ""


def test_git_link_emit_event_adhoc_branch_emits_nothing(capsys):
    # Arrange
    argv = [
        "emit-event",
        "--repo",
        "scitex-cards",
        "--branch",
        "develop",
        "--sha",
        "abc123",
    ]
    # Act
    _main(argv)
    # Assert
    assert capsys.readouterr().out == ""


def test_output_module_has_no_bare_print_call():
    # Arrange
    source = Path(_output_module.__file__).read_text(encoding="utf-8")
    # Act
    found = _bare_print_call_count(source)
    # Assert
    assert found == 0


def test_git_link_module_has_no_bare_print_call():
    # Arrange
    source = Path(_git_link_module.__file__).read_text(encoding="utf-8")
    # Act
    found = _bare_print_call_count(source)
    # Assert
    assert found == 0


def test_bare_print_detector_is_not_vacuous():
    # Arrange
    with_print = 'print("x")\n'
    without_print = '"""bare ``print`` is only prose here."""\n'
    # Act
    found = (_bare_print_call_count(with_print), _bare_print_call_count(without_print))
    # Assert
    assert found == (1, 0)
