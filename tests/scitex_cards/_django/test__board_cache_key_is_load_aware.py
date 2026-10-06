#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board cache tells readers apart, not just stores.

``get_board`` used to key its cache on the store path alone, which was sound
while every caller read the whole document: one store, one board. The moment
a caller passes ``load=`` — the project-scoped readers the scoped-read card
is wiring in — the same store has as many boards as readers, and a shared
key serves one reader's slice as another reader's board. The key is now
``(store path, load tag)``, built in exactly one place.

These cases need no store: the key is a pure function of path and loader,
and the store-backed cache behaviour (hit, stale-while-revalidate, storm
guard) is the stale suite's subject, on a host with a writable cluster.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import services  # noqa: E402

_STORE = Path("/tmp/fleet-store-identity/tasks.yaml")


def _whole_document_loader(path):
    return ["whole"]


def _project_scoped_loader(path):
    return ["scoped"]


def test_the_default_read_tags_default():
    """``load=None`` is the whole-document read, named, not implied."""
    # Arrange
    # Act
    tag = services._load_tag(None)
    # Assert
    assert tag == "default"


def test_two_named_loaders_tag_differently():
    """The case the old key got wrong: same store, two readers, one entry."""
    # Arrange
    # Act
    tags = (
        services._load_tag(_whole_document_loader),
        services._load_tag(_project_scoped_loader),
    )
    # Assert
    assert tags[0] != tags[1]


def test_the_same_loader_tags_stably():
    """A tag that moves between calls is a cache that never hits."""
    # Arrange
    # Act
    tags = (
        services._load_tag(_project_scoped_loader),
        services._load_tag(_project_scoped_loader),
    )
    # Assert
    assert tags[0] == tags[1]


def test_the_key_pairs_the_store_with_how_it_was_read():
    """Both halves, in order: which store, which reader."""
    # Arrange
    # Act
    key = services._board_cache_key(_STORE, _project_scoped_loader)
    # Assert
    assert key == (str(_STORE), services._load_tag(_project_scoped_loader))


def test_an_explicit_loader_is_its_own_entry():
    """Passing the default reader explicitly duplicates one entry, harmlessly.

    Sharing it instead would require the key to KNOW which callable is the
    default — a coupling that breaks the moment the default changes. A
    duplicate entry is never a wrong board; a shared one can be.
    """
    # Arrange
    # Act
    keys = (
        services._board_cache_key(_STORE, None),
        services._board_cache_key(_STORE, services._load_global_tasks),
    )
    # Assert
    assert keys[0] != keys[1]


def test_a_loader_without_a_qualname_still_keys():
    """``functools.partial`` carries no ``__qualname__``; the key must not."""
    # Arrange
    loader = functools.partial(_project_scoped_loader)
    # Act
    keys = (
        services._board_cache_key(_STORE, loader),
        services._board_cache_key(_STORE, loader),
    )
    # Assert
    assert keys[0] == keys[1]


def test_the_key_is_built_in_exactly_one_place():
    """THE structural guard. A second construction site is a second cache
    that can disagree with the first — which is the defect, relocated."""
    # Arrange
    source = Path(services.__file__).read_text("utf-8")
    # Act
    constructions = source.count("key = _board_cache_key(resolved, load)")
    leftovers = source.count("key = str(resolved)")
    # Assert
    assert (constructions, leftovers) == (1, 0)
