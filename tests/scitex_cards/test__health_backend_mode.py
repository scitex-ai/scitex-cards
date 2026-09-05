#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The doctor must name the engine, and must fail when the two rails differ.

Operator directive 2026-08-02: "fail fast, fail loud, no fallbacks", and asking
for a doctor that says which engine this process is on.

TWO DEFECTS THIS PINS.

First, ``check_single_write_target`` reported ONE ENGINE'S NAME
UNCONDITIONALLY. That was true when written and became a lie the day a store
could be a PostgreSQL server: the doctor answered "which engine am I on?" — the
exact question that line looks like it answers — with the wrong engine,
confidently, on every PostgreSQL deployment.

Second, nothing reported the NOTIFICATION rail's engine at all. The inbox was a
sidecar located from the store PATH, so pointing the store at a server did not
move it: cards went to PostgreSQL and notifications stayed on the sidecar. That
split is what let a DM commit to the store on 2026-08-01 while no notification
was ever created, with every card-side check green.

WHAT THE ANSWER "A FILE" BECAME. There is one storage engine now, so a target
that is not a DSN does not select a different engine — it names no store, and
the token for that is ``UNSUPPORTED``. The inbox rail's answer for the same
target is ``UNAVAILABLE``: not "on another engine", but "nothing to select".
The two tokens are deliberately distinct from each other and from a SPLIT,
because all three call for different actions and folding them together is how
a doctor starts answering a question nobody asked.

WHY A SPLIT IS A FAILURE, NOT AN INFO LINE. A check that merely printed both
modes would report the split as normal. It is not normal — it is the state in
which a green card-side doctor says nothing about whether notifications are
delivered. So the doctor stays red until the rails agree.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._config import CONFIG_NAME, STORE_SECTION, STORE_TARGET_KEY
from scitex_cards._health_backend_mode import (
    POSTGRES,
    UNSUPPORTED,
    check_backend_mode,
)
from scitex_cards._health_write_target import check_single_write_target
from scitex_cards._paths import _user_root

#: A well-formed DSN. Never CONNECTED to -- ``check_backend_mode`` inspects
#: the target string and never opens it -- but spelled with the port this
#: fleet actually runs on, because 5432 appearing anywhere teaches the wrong
#: port to the next reader (operator ruling; see
#: ``test__store_url_attempted_dsn`` for the same rule applied to messages).
_DSN = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
_MANAGED = ("SCITEX_CARDS_DB", "HOME", "SCITEX_DIR", "SCITEX_CARDS_INBOX_BACKEND")


def _write_user_config(target: str) -> None:
    """Point the user-scope config file at ``target``.

    Written through the package's own ``_user_root`` rather than a hand-built
    ``HOME/.scitex/cards`` path, so a test that claims to exercise the config
    tier cannot silently write somewhere the resolver never reads.
    """
    import json

    root = _user_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(
        json.dumps({STORE_SECTION: {STORE_TARGET_KEY: target}}),
        encoding="utf-8",
    )


@pytest.fixture
def file_store(tmp_path):
    """A card store target that is a plain FILENAME.

    Not "a store on the other engine" -- there is no other engine. This is a
    target that names no store at all, which is the condition both rails have
    to report honestly: ``UNSUPPORTED`` for the cards, ``UNAVAILABLE`` for the
    inbox, and a failing check rather than a fallback (operator ruling
    2026-08-23).
    """
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    for name in ("SCITEX_DIR", "SCITEX_CARDS_INBOX_BACKEND"):
        os.environ.pop(name, None)
    os.environ["HOME"] = str(tmp_path)
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    store = tmp_path / ".scitex" / "cards" / "cards.db"
    os.environ["SCITEX_CARDS_DB"] = str(store)
    os.chdir(tmp_path)

    yield str(store)

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture
def postgres_rails(file_store):
    """Both rails on a server: the shape the fleet has run since #780.

    Layered on ``file_store`` so the environment is saved and restored once.
    Real environment variables, because which backend the rail picks is read
    from ``os.environ`` and that resolution is exactly what is under test.
    """
    os.environ["SCITEX_CARDS_DB"] = _DSN
    yield _DSN


@pytest.fixture
def postgres_inbox_only(file_store):
    """Inbox on a server, cards in a file — the split the other way round."""
    os.environ["SCITEX_CARDS_INBOX_BACKEND"] = "postgres"
    os.environ["SCITEX_CARDS_INBOX_DSN"] = _DSN
    yield file_store
    os.environ.pop("SCITEX_CARDS_INBOX_DSN", None)


class TestAFileStoreHasNoInboxBackend:
    """A target that names no store gives the inbox rail nothing to select.

    There is no fallback rail (operator ruling 2026-08-23), so this is a
    failure and not an informational line.
    """

    def test_a_file_store_fails(self, file_store):
        # Arrange
        store = file_store

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is False

    def test_it_names_the_card_store_mode(self, file_store):
        # Arrange
        store = file_store

        # Act
        result = check_backend_mode(store)

        # Assert
        assert UNSUPPORTED in result["detail"]


class TestASplitIsReportedAsFailure:
    def test_a_server_store_with_no_usable_inbox_fails(self, file_store):
        """Cards on PostgreSQL while the ambient target leaves the rail nothing."""
        # Arrange
        store = _DSN

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is False

    def test_the_detail_names_the_card_engine_and_the_missing_rail(
        self, file_store
    ):
        # Arrange
        store = _DSN

        # Act
        detail = check_backend_mode(store)["detail"].lower()

        # Assert — the card store's engine and the fact that the inbox rail has
        # NO backend. It used to assert the refusal named a second engine; with
        # one engine there is no second name to print, and "no usable backend"
        # is the stronger statement anyway.
        assert POSTGRES in detail and "no usable backend" in detail

    def test_the_hint_names_the_actual_remedy(self, file_store):
        """A knob here would be a fallback wearing a switch -- the hint names
        the one real fix (move the store) rather than offering a toggle."""
        # Arrange
        store = _DSN

        # Act
        hint = check_backend_mode(store)["hint"]

        # Assert
        assert "SCITEX_CARDS_DB" in hint

    def test_it_does_not_raise_on_a_nonsense_store(self, file_store):
        """A doctor reports; it must not crash the caller asking for a report."""
        # Arrange
        store = "://///not-a-store"

        # Act
        result = check_backend_mode(store)

        # Assert
        assert isinstance(result["ok"], bool)


class TestItCanGoGreenWhenTheRailMoves:
    """A doctor that cannot report a recovery costs as much as one that cannot
    report a fault.

    Measured on a live container 2026-08-11, AFTER the rail moved into
    PostgreSQL in #780: this check still reported ``SPLIT BACKENDS ... the
    notification inbox is on yaml (~/.scitex/cards/runtime/inboxes.json)`` — a
    path that did not exist on disk — because ``_inbox_mode`` asked the
    two-valued engine predicate and mapped its ``False`` onto "yaml". The
    remedy the hint named had already been applied and the check could not say
    so, which is the same class of error it exists to catch, pointed the other
    way.
    """

    def test_both_rails_on_postgres_is_ok(self, postgres_rails):
        # Arrange
        store = postgres_rails

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is True

    def test_the_detail_does_not_claim_a_json_sidecar(self, postgres_rails):
        """It named a file that was not there; that is worse than saying less."""
        # Arrange
        store = postgres_rails

        # Act
        detail = check_backend_mode(store)["detail"]

        # Assert
        assert "inboxes.json" not in detail

    def test_the_inbox_is_reported_as_postgres(self, postgres_rails):
        # Arrange
        store = postgres_rails

        # Act
        detail = check_backend_mode(store)["detail"]

        # Assert
        assert f"both rails on {POSTGRES}" in detail


class TestASplitTheOtherWayIsAlsoReported:
    def test_a_postgres_inbox_with_a_file_store_fails(self, postgres_inbox_only):
        """Notifications referencing cards their database has never seen."""
        # Arrange
        store = postgres_inbox_only

        # Act
        result = check_backend_mode(store)

        # Assert
        assert result["ok"] is False

    def test_its_hint_does_not_tell_you_to_move_the_inbox(self, postgres_inbox_only):
        """The inbox is already where it belongs; the STORE is the odd one."""
        # Arrange
        store = postgres_inbox_only

        # Act
        hint = check_backend_mode(store)["hint"]

        # Assert
        assert "SCITEX_CARDS_DB" in hint


class TestItNamesWhichTierChoseTheTarget:
    """ "I edited the config and nothing changed" must be one line, not a hunt."""

    def test_an_explicit_argument_is_named(self, file_store):
        # Arrange
        store = file_store

        # Act
        detail = check_backend_mode(store)["detail"]

        # Assert
        assert "explicit argument" in detail

    def test_the_environment_variable_is_named_when_it_wins(self, file_store):
        """The env var outranks the file -- that is the confusing case."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = file_store

        # Act
        detail = check_backend_mode(None)["detail"]

        # Assert
        assert "environment variable" in detail

    def test_the_config_file_is_named_when_no_env_var_is_set(self, file_store):
        """It must name ``config.json`` -- not merely resolve to SOMETHING.

        THIS TEST WAS GREEN FOR THE WRONG REASON until the compat shim was
        deleted. It asserted only ``"chosen by" in detail``, which every tier
        satisfies, and it wrote no config file at all -- so the tier it is named
        after was never exercised. What actually answered was the deleted
        ``_env_compat`` module: it mirrored the ambient ``SCITEX_CARDS_DB`` onto
        the retired env name AT IMPORT, the fixture did not manage that name,
        and popping ``SCITEX_CARDS_DB`` therefore left the REAL production
        PostgreSQL DSN visible through the retired one. Measured 2026-08-16:
        with the env popped, ``resolve_store_target`` returned
        ``postgresql://...:55432/scitex_cards``. A unit test was reading the
        fleet's live store and calling that a config-file lookup.

        So it now WRITES the config file and asserts the file is NAMED. Both
        halves matter: without the write there is no config tier to find, and
        without naming the file the assertion passes on any tier -- which is
        exactly how it hid a leak of production state for as long as it did.
        """
        # Arrange
        os.environ.pop("SCITEX_CARDS_DB", None)
        _write_user_config(file_store)

        # Act
        detail = check_backend_mode(None)["detail"]

        # Assert
        assert CONFIG_NAME in detail


class TestTheWriteTargetNamesTheRealEngine:
    def test_it_no_longer_hardcodes_one_engine(self, file_store):
        """Against a target that names no store, the honest answer says so.

        This test and its docstring were both named after the retired engine,
        and both asserted that a FILE store honestly reports that engine. That
        was true of a two-engine world and is now the opposite of true: a
        filename does not select an engine, it selects nothing, and printing
        any engine's name here would be the very hardcoding this test was
        written to prevent -- just with a different constant.
        """
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = file_store

        # Act
        detail = check_single_write_target()["detail"]

        # Assert
        assert UNSUPPORTED in detail

    def test_it_reports_postgres_when_the_store_is_a_server(self, file_store):
        """The regression: this line used to name the wrong engine here too."""
        # Arrange
        os.environ["SCITEX_CARDS_DB"] = _DSN

        # Act
        detail = check_single_write_target()["detail"]

        # Assert
        assert POSTGRES in detail


# EOF
