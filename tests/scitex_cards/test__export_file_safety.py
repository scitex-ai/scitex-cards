"""Actual CLI, private PostgreSQL and filesystem export boundaries."""

import click
import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._export_file import check_new_destination, write_new_document
from scitex_cards._store import add_task
from scitex_cards._users import register_user


@pytest.fixture
def saved_card(new_store, monkeypatch):
    dsn = new_store()
    monkeypatch.setenv("SCITEX_STORE_DSN", dsn)
    actor = register_user(kind="agent", names=["export-author"], store=dsn)
    return add_task(
        store=dsn,
        assignee=actor.id,
        created_by=actor.id,
        id="export-safety",
        title="Saved export",
        status="done",
        scope="export-tests",
    )


def test_stdout_export_reads_the_real_private_store(saved_card):
    # Arrange
    command = ["export", "--scope", "export-tests", "--group-by", "none"]
    # Act
    result = CliRunner().invoke(main, command)
    # Assert
    assert (result.exit_code, result.output) == (0, "- [x] Saved export\n")


def test_new_file_requires_explicit_noninteractive_approval(tmp_path, saved_card):
    # Arrange
    output = tmp_path / "tasks.md"
    # Act
    result = CliRunner().invoke(main, ["export", "--output", str(output)])
    # Assert
    assert (result.exit_code, output.exists(), "requires --yes" in result.output) == (
        2,
        False,
        True,
    )


def test_preview_creates_no_output_or_temporary_files(tmp_path, saved_card):
    # Arrange
    output = tmp_path / "tasks.md"
    # Act
    result = CliRunner().invoke(main, ["export", "--output", str(output), "--dry-run"])
    # Assert
    assert (
        result.exit_code,
        list(tmp_path.iterdir()),
        "[dry-run]" in result.output,
    ) == (0, [], True)


def test_approved_export_publishes_exact_real_store_bytes(tmp_path, saved_card):
    # Arrange
    output = tmp_path / "tasks.md"
    # Act
    result = CliRunner().invoke(
        main,
        [
            "export",
            "--scope",
            "export-tests",
            "--group-by",
            "none",
            "--output",
            str(output),
            "--yes",
        ],
    )
    # Assert
    assert (
        result.exit_code,
        output.read_bytes(),
        sorted(p.name for p in tmp_path.iterdir()),
    ) == (0, b"- [x] Saved export\n", ["tasks.md"])


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_existing_destination_entries_are_preserved(tmp_path, saved_card, kind):
    # Arrange
    original = tmp_path / "original.md"
    original.write_bytes(b"keep original\n")
    output = tmp_path / "tasks.md"
    if kind == "file":
        output.write_bytes(b"keep destination\n")
    elif kind == "directory":
        output.mkdir()
    else:
        output.symlink_to(original if kind == "symlink" else tmp_path / "missing")
    before = output.lstat()
    expected_exit = 2 if kind == "directory" else 1
    expected_hint = "is a directory" if kind == "directory" else "already exists"
    # Act
    result = CliRunner().invoke(main, ["export", "--output", str(output), "--yes"])
    # Assert
    assert (
        result.exit_code,
        original.read_bytes(),
        output.lstat().st_ino,
        expected_hint in result.output,
    ) == (expected_exit, b"keep original\n", before.st_ino, True)


def test_atomic_publication_never_replaces_an_existing_file(tmp_path):
    # Arrange
    output = tmp_path / "tasks.md"
    check_new_destination(output)
    output.write_bytes(b"racing writer\n")
    failure = None
    # Act

    try:
        write_new_document(output, b"new contents\n")
    except click.ClickException as exc:
        failure = exc
    # Assert
    assert (
        isinstance(getattr(failure, "__cause__", None), FileExistsError),
        output.read_bytes(),
        sorted(p.name for p in tmp_path.iterdir()),
    ) == (True, b"racing writer\n", ["tasks.md"])


def test_preview_does_not_open_an_unavailable_store(tmp_path):
    # Arrange
    output = tmp_path / "tasks.md"
    socket = tmp_path / "absent-socket"
    unavailable = f"postgresql://postgres@/postgres?host={socket}&connect_timeout=1"
    # Act
    result = CliRunner().invoke(
        main,
        ["export", "--output", str(output), "--dry-run"],
        env={"SCITEX_STORE_DSN": unavailable},
    )
    # Assert
    assert (result.exit_code, list(tmp_path.iterdir())) == (0, [])
