"""Publish a complete new document without replacing another directory entry."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click


def check_new_destination(path: Path) -> None:
    """Refuse existing files, directories and even dangling symlinks."""
    if os.path.lexists(path):
        raise click.ClickException(
            f"Output already exists; choose a new filename: {path}"
        )
    if not path.parent.is_dir():
        raise click.ClickException(f"Output directory does not exist: {path.parent}")


def write_new_document(path: Path, payload: bytes) -> None:
    """Write privately, then atomically link without clobbering a racing target.

    Link publication requires filesystem hard-link support; an unsupported
    filesystem fails closed. No overwrite or crash-durability promise is made.
    """
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except OSError as exc:
        raise click.ClickException(
            f"Could not create new output {path}: {exc}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
