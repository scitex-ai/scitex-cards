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


def test_resolver_reads_the_scitex_cards_dist():
    """The resolver asks for THIS dist by name and returns what it is told."""
    # Arrange — a real reader with importlib.metadata.version's contract. The
    # dict lookup is the assertion's other half: a resolver that asked for a
    # different dist name raises KeyError here rather than quietly passing.
    asked = {"scitex-cards": "9.9.9"}

    # Act
    resolved = scitex_cards._resolve_version(version_of=lambda dist: asked[dist])

    # Assert
    assert resolved == "9.9.9"


# REMOVED: test_resolver_falls_back_to_scitex_cards_dist.
#
# It pinned the SECOND tier of `_resolve_version()`: when the current dist name
# was not installed, fall back to the pre-rename one, so an un-cutover editable
# install still reported a version. That tier is gone with the retired dist, and
# the loop it lived in had already collapsed to iterating the SAME name twice —
# a "fallback" whose second attempt could only re-raise the first's
# PackageNotFoundError. Its own fake made that visible: `_version` raised for
# "scitex-cards" and returned 8.8.8 for anything else, so post-rename it was
# asserting that a name nothing asks for supplies the version.
#
# The tier below it — neither dist installed, report the local sentinel — is
# still real and is still covered by the test immediately following.


def test_resolver_falls_back_to_local_when_uninstalled():
    """Running from a source tree with the dist not installed.

    The reader here RAISES the error the stdlib raises, rather than the
    module attribute being swapped — so this exercises the same `except
    PackageNotFoundError` branch by making the failure happen, which is what
    an uninstalled tree actually does to the resolver.
    """
    # Arrange
    import importlib.metadata as md

    def _uninstalled(dist):
        raise md.PackageNotFoundError(dist)

    # Act
    resolved = scitex_cards._resolve_version(version_of=_uninstalled)

    # Assert
    assert resolved == "0.0.0+local"


# EOF
