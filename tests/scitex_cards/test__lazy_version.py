#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex_cards.__version__` resolves lazily, and stays lazy.

`importlib.metadata` is expensive to import — measured at 223 ms of a
425 ms cold `import scitex_cards`, because reading package metadata drags
in email.message, email.utils and zipfile. The package therefore resolves
`__version__` through its PEP 562 `__getattr__` instead of at module
scope, which keeps cold start well inside the audit-cli §10 budget
(500 ms).

The load-bearing test here is `test_import_does_not_load_importlib_metadata`:
it fails against the eager version of `__init__.py`, so it is what stops
the optimisation from being silently undone by a future edit. The rest
pin the public surface that the optimisation must not change.
"""

from __future__ import annotations

import os
import subprocess
import sys

import scitex_cards


def _run_probe(snippet: str) -> str:
    """Run `snippet` in a fresh interpreter that imports THIS scitex_cards.

    The path is inherited from the running `sys.path` rather than rebuilt,
    so the probe cannot silently measure a different (e.g. site-packages)
    copy of the package than the one under test.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_import_does_not_load_importlib_metadata():
    """The whole point: importing the package must not pay for metadata."""
    # Arrange
    probe = "import sys, scitex_cards;print('importlib.metadata' in sys.modules)"

    # Act
    loaded = _run_probe(probe)

    # Assert
    assert loaded == "False"


def test_touching_version_does_load_importlib_metadata():
    """Deferred, not deleted — asking for a version still reads metadata."""
    # Arrange
    probe = (
        "import sys, scitex_cards;"
        "scitex_cards.__version__;"
        "print('importlib.metadata' in sys.modules)"
    )

    # Act
    loaded = _run_probe(probe)

    # Assert
    assert loaded == "True"


def test_version_is_a_string():
    """Attribute access resolves through __getattr__."""
    # Arrange
    # (module imported at test-module scope)

    # Act
    version = scitex_cards.__version__

    # Assert
    assert isinstance(version, str)


def test_version_is_importable_by_name():
    """`from scitex_cards import __version__` — the form _django/views.py uses.

    A `from`-import takes a different path than attribute access, so the
    two real call sites in `_django/views.py` need their own coverage.
    """
    # Arrange
    probe = "from scitex_cards import __version__; print(__version__)"

    # Act
    version = _run_probe(probe)

    # Assert
    assert version


def test_version_is_cached_after_first_access():
    """PEP 562 caches into globals(), so repeat reads skip the resolver."""
    # Arrange
    scitex_cards.__version__

    # Act
    cached = vars(scitex_cards)["__version__"]

    # Assert
    assert isinstance(cached, str)


def test_version_appears_in_dir():
    """Tab-completion must still see it before anything touches it."""
    # Arrange
    names = dir(scitex_cards)

    # Act
    present = "__version__" in names

    # Assert
    assert present


def test_unknown_attribute_still_raises():
    """The __version__ branch must not swallow the PEP 562 contract."""
    # Arrange
    missing = "definitely_not_a_public_name"

    # Act
    raised = None
    try:
        getattr(scitex_cards, missing)
    except AttributeError as exc:
        raised = exc

    # Assert
    assert raised is not None


def test_resolver_prefers_scitex_cards_dist(monkeypatch):
    """Both dists installed during the transition — the new name wins."""
    # Arrange
    import importlib.metadata as md

    monkeypatch.setattr(md, "version", lambda dist: {"scitex-cards": "9.9.9"}[dist])

    # Act
    resolved = scitex_cards._resolve_version()

    # Assert
    assert resolved == "9.9.9"


def test_resolver_falls_back_to_scitex_todo_dist(monkeypatch):
    """Un-cutover editable installs still only carry the old dist name."""
    # Arrange
    import importlib.metadata as md

    def _version(dist):
        if dist == "scitex-cards":
            raise md.PackageNotFoundError(dist)
        return "8.8.8"

    monkeypatch.setattr(md, "version", _version)

    # Act
    resolved = scitex_cards._resolve_version()

    # Assert
    assert resolved == "8.8.8"


def test_resolver_falls_back_to_local_when_uninstalled(monkeypatch):
    """Running from a source tree with neither dist installed."""
    # Arrange
    import importlib.metadata as md

    def _version(dist):
        raise md.PackageNotFoundError(dist)

    monkeypatch.setattr(md, "version", _version)

    # Act
    resolved = scitex_cards._resolve_version()

    # Assert
    assert resolved == "0.0.0+local"


# EOF
