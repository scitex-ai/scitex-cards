#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The version string can lie. These tests prove the probe catches it when it does.

Regression cover for the 2026-07-12 incident: an orphaned ``.dist-info`` froze
``scitex-todo`` at 0.7.26 while the code actually running was 0.8.7 — thirty
releases apart, permanently, with nothing reporting a problem. sac reproduced the
same shape independently in its own container (baked dist-info over bound code).

The load-bearing tests here are the ``fossilised_probe`` cluster — a SYNTHETIC
FOSSIL. Testing only against a healthy install would prove nothing: the whole
point is the case where metadata and code disagree.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from scitex_cards._install_probe import (
    KIND_ABSENT,
    KIND_EDITABLE,
    KIND_ORPHANED,
    KIND_WHEEL,
    check_install_honest,
    probe_install,
)


def _make_source_tree(root, pkg: str, version: str) -> None:
    """A minimal editable-install-shaped source tree: pyproject + package."""
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{pkg.replace("_", "-")}"
            version = "{version}"
            """
        ).strip(),
        encoding="utf-8",
    )
    src = root / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("MARKER = True\n", encoding="utf-8")


@pytest.fixture
def fake_pkg(tmp_path, monkeypatch):
    """An importable package living in a source tree (the editable shape)."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_source_tree(root, "fakepkg", "2.0.0")
    monkeypatch.syspath_prepend(str(root / "src"))
    yield root
    sys.modules.pop("fakepkg", None)


@pytest.fixture
def wheel_pkg_with_two_distinfos(tmp_path, monkeypatch):
    """A site-packages layout with TWO .dist-info dirs for one package.

    The exact shape observed on 2026-07-12: upgrading 0.7.50 -> 0.9.0 in the agent
    venv left the old dist-info sitting next to the new one.
    """
    site = tmp_path / "site-packages"
    pkg = site / "wheelpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("NEW_SYMBOL = True\n", encoding="utf-8")
    (site / "wheelpkg-0.9.0.dist-info").mkdir()
    (site / "wheelpkg-0.7.50.dist-info").mkdir()  # the orphaned fossil
    monkeypatch.syspath_prepend(str(site))
    yield site
    sys.modules.pop("wheelpkg", None)


# --------------------------------------------------------------------------
# probe fixtures — one per install shape under test
# --------------------------------------------------------------------------
@pytest.fixture
def fossilised_probe(fake_pkg, monkeypatch):
    """Metadata says 1.0.0; the code on disk is 2.0.0."""
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "1.0.0")
    return probe_install("fakepkg")


@pytest.fixture
def agreeing_probe(fake_pkg, monkeypatch):
    """Metadata and source agree at 2.0.0 — the healthy shape."""
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "2.0.0")
    return probe_install("fakepkg")


@pytest.fixture
def orphaned_probe(monkeypatch):
    """Metadata present, code absent — every version check "passes" on nothing."""
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.26"
    )
    return probe_install("ghostpkg")


@pytest.fixture
def absent_probe():
    """No metadata AND no code = simply not installed."""
    return probe_install("definitely-not-installed-xyz")


@pytest.fixture
def exploding_metadata_probe(fake_pkg, monkeypatch):
    """The metadata backend raises; the probe must still return a verdict."""

    def boom(dist):
        raise RuntimeError("metadata backend exploded")

    monkeypatch.setattr("scitex_cards._install_probe._md.version", boom)
    return probe_install("fakepkg")  # must not raise


@pytest.fixture
def unknowable_code_version_probe(fake_pkg, monkeypatch):
    """The source drops its version claim → the code's real version is unknowable."""
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "1.0.0")
    (fake_pkg / "pyproject.toml").write_text(
        '[project]\nname = "fakepkg"\n', encoding="utf-8"
    )
    return probe_install("fakepkg")


@pytest.fixture
def health_check_result(fake_pkg, monkeypatch):
    """The doctor adapter's result for a LYING install."""
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "1.0.0")
    return check_install_honest("fakepkg")


@pytest.fixture
def post_upgrade_probe(fake_pkg, monkeypatch):
    """The DISK grows a symbol AFTER the module is already imported."""
    import fakepkg  # noqa: F401  - force it into sys.modules ("the running process")

    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "2.0.0")
    # The exact shape of "someone pip-upgraded under a running server".
    (fake_pkg / "src" / "fakepkg" / "__init__.py").write_text(
        "MARKER = True\nPOST_UPGRADE_SYMBOL = True\n", encoding="utf-8"
    )
    return probe_install(
        "fakepkg", features={"post_upgrade": "fakepkg:POST_UPGRADE_SYMBOL"}
    )


@pytest.fixture
def ambiguous_probe(wheel_pkg_with_two_distinfos, monkeypatch):
    """Two dist-infos, and metadata picks the FOSSIL — exactly as it did live."""
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )
    return probe_install("wheelpkg")


@pytest.fixture
def solo_distinfo_probe(tmp_path, monkeypatch):
    """A wheel layout with exactly ONE .dist-info — the unambiguous case."""
    site = tmp_path / "site-packages"
    pkg = site / "solopkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (site / "solopkg-1.0.0.dist-info").mkdir()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "1.0.0")
    yield probe_install("solopkg")
    sys.modules.pop("solopkg", None)


# --------------------------------------------------------------------------
# THE BUG THIS EXISTS FOR.
# --------------------------------------------------------------------------

#: WHY the `fossilised_probe` cluster below is split but shares one story:
#: metadata says 1.0.0; the code on disk is 2.0.0. The probe must NOT be fooled.
#: This is the incident, reproduced: a .dist-info that outlived the code it
#: describes. A version-string check reports 1.0.0 and is confidently wrong.


def test_fossil_is_classified_as_an_editable_install(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    kind = p.kind
    # Assert
    assert kind == KIND_EDITABLE


def test_fossil_metadata_version_is_the_stale_one(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    metadata_version = p.metadata_version
    # Assert — the fossil.
    assert metadata_version == "1.0.0"


def test_fossil_code_version_is_the_real_one(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    code_version = p.code_version
    # Assert — the truth.
    assert code_version == "2.0.0"


def test_detects_a_fossilised_dist_info(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    honest = p.honest
    # Assert — metadata and code disagree, so the install is NOT honest.
    assert honest is False


def test_fossil_version_string_is_never_trusted(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False, "a drifted version string must never be trusted"


def test_fossil_detail_says_the_metadata_lies(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    detail = p.detail
    # Assert
    assert "LIES" in detail


def test_fossil_hint_names_the_fossil(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    hint = p.hint or ""
    # Assert
    assert "FOSSIL" in hint


def test_fossil_hint_names_the_actual_repair(fossilised_probe):
    # Arrange
    p = fossilised_probe
    # Act
    hint = p.hint or ""
    # Assert — the hint must name the repair, not just complain.
    assert "--no-deps" in hint


def test_agreeing_metadata_is_honest(agreeing_probe):
    """When the metadata matches the source, the version string IS usable."""
    # Arrange
    p = agreeing_probe
    # Act
    honest = p.honest
    # Assert
    assert honest is True


def test_agreeing_metadata_is_trustworthy(agreeing_probe):
    # Arrange
    p = agreeing_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is True


def test_agreeing_metadata_reports_the_code_version(agreeing_probe):
    # Arrange
    p = agreeing_probe
    # Act
    code_version = p.code_version
    # Assert
    assert code_version == "2.0.0"


# --------------------------------------------------------------------------
# The other ways an install can be untrustworthy.
# --------------------------------------------------------------------------


def test_orphaned_distinfo_with_no_code_is_the_worst_case(orphaned_probe):
    """Metadata present, code absent: every version check "passes" against nothing."""
    # Arrange
    p = orphaned_probe
    # Act
    kind = p.kind
    # Assert
    assert kind == KIND_ORPHANED


def test_orphaned_install_is_never_trustworthy(orphaned_probe):
    # Arrange
    p = orphaned_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False


def test_orphaned_hint_names_the_orphan(orphaned_probe):
    # Arrange
    p = orphaned_probe
    # Act
    hint = p.hint or ""
    # Assert
    assert "ORPHANED" in hint


def test_orphaned_hint_names_the_repair(orphaned_probe):
    # Arrange
    p = orphaned_probe
    # Act
    hint = p.hint or ""
    # Assert
    assert "force-reinstall" in hint


#: WHY the `absent_probe` cluster below is split but shares one story:
#: no metadata AND no code = simply not installed. NOT a fossil. Calling this
#: "orphaned" would send the reader hunting for a .dist-info that does not
#: exist — a confidently wrong hint, which is the very disease this module
#: treats. (Caught by dogfooding the probe on a nonexistent package.)


def test_absent_package_is_classified_absent(absent_probe):
    # Arrange
    p = absent_probe
    # Act
    kind = p.kind
    # Assert
    assert kind == KIND_ABSENT


def test_absent_package_is_not_trustworthy(absent_probe):
    # Arrange
    p = absent_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False


def test_absent_package_detail_says_not_installed(absent_probe):
    # Arrange
    p = absent_probe
    # Act
    detail = p.detail
    # Assert
    assert "not installed" in detail


def test_absent_package_is_not_reported_as_orphaned(absent_probe):
    # Arrange
    p = absent_probe
    # Act
    hint = p.hint or ""
    # Assert — calling this orphaned would send the reader on a fossil hunt.
    assert "ORPHANED" not in hint


def test_absent_package_hint_does_not_mention_dist_info(absent_probe):
    # Arrange
    p = absent_probe
    # Act
    hint = p.hint or ""
    # Assert
    assert ".dist-info" not in hint


def test_probe_never_raises_even_when_metadata_blows_up(exploding_metadata_probe):
    """A probe that can crash is a probe that gets wrapped in try/except and ignored."""
    # Arrange
    p = exploding_metadata_probe
    # Act
    probe_error = p.probe_error
    # Assert — it returned a verdict carrying the failure, instead of raising.
    assert probe_error is not None


def test_probe_error_carries_the_backend_message(exploding_metadata_probe):
    # Arrange
    p = exploding_metadata_probe
    # Act
    probe_error = p.probe_error
    # Assert
    assert "exploded" in probe_error


def test_exploding_metadata_is_never_trustworthy(exploding_metadata_probe):
    # Arrange
    p = exploding_metadata_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False, "an unverifiable install is never 'fine'"


def test_unknowable_code_version_is_none(unknowable_code_version_probe):
    """'I could not check' must never render as 'it is fine'."""
    # Arrange
    p = unknowable_code_version_probe
    # Act
    code_version = p.code_version
    # Assert
    assert code_version is None


def test_unverifiable_is_never_reported_as_honest(unknowable_code_version_probe):
    # Arrange
    p = unknowable_code_version_probe
    # Act
    honest = p.honest
    # Assert
    assert honest is False


def test_unverifiable_is_never_trustworthy(unknowable_code_version_probe):
    # Arrange
    p = unknowable_code_version_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False


def test_unverifiable_detail_says_cannot_be_confirmed(unknowable_code_version_probe):
    # Arrange
    p = unknowable_code_version_probe
    # Act
    detail = p.detail
    # Assert
    assert "cannot be confirmed" in detail


# --------------------------------------------------------------------------
# Content probing — the check that needs no version at all.
# --------------------------------------------------------------------------


def test_features_probe_the_code_directly_bypassing_versions_entirely(
    fake_pkg, monkeypatch
):
    """The strongest check: does the symbol I expect actually exist?

    This answers "is the code I think I deployed really here?" without trusting
    any version string — the only check a fossil cannot defeat.
    """
    # Arrange
    monkeypatch.setattr("scitex_cards._install_probe._md.version", lambda dist: "2.0.0")
    # Act
    p = probe_install(
        "fakepkg",
        features={
            "present": "fakepkg:MARKER",
            "absent": "fakepkg:NOT_THERE",
            "bad_module": "no_such_module_at_all:X",
        },
    )
    # Assert
    assert p.features == {"present": True, "absent": False, "bad_module": False}


def test_real_scitex_cards_install_is_probeable():
    """Dogfood: probe the package under test. Whatever the shape, it must classify."""
    # Arrange
    known_kinds = (KIND_WHEEL, KIND_EDITABLE, KIND_ORPHANED, KIND_ABSENT)
    # Act
    p = probe_install("scitex-cards")
    # Assert
    assert p.kind in known_kinds


def test_real_install_probe_is_never_silent():
    # Arrange
    package = "scitex-cards"
    # Act
    p = probe_install(package)
    # Assert — a probe with no detail tells its reader nothing.
    assert p.detail


def test_real_install_untrustworthy_carries_a_hint():
    # Arrange
    package = "scitex-cards"
    # Act
    p = probe_install(package)
    # Assert — an untrustworthy install MUST carry an actionable hint.
    assert p.trustworthy or bool(p.hint)


# --------------------------------------------------------------------------
# The health-doctor adapter.
# --------------------------------------------------------------------------


def test_health_check_contract(health_check_result):
    """The doctor check returns {ok, detail, hint}."""
    # Arrange
    res = health_check_result
    # Act
    keys = set(res)
    # Assert
    assert keys == {"ok", "detail", "hint"}


def test_health_check_ok_is_false_when_lying(health_check_result):
    # Arrange
    res = health_check_result
    # Act
    ok = res["ok"]
    # Assert — ok is False exactly when the install is lying.
    assert ok is False


def test_health_check_failure_always_hints(health_check_result):
    # Arrange
    res = health_check_result
    # Act
    hint = res["hint"]
    # Assert
    assert hint, "a failing doctor check must always hint at the next step"


# --------------------------------------------------------------------------
# The THIRD failure mode: disk truth is not process truth.
# --------------------------------------------------------------------------

#: WHY the `post_upgrade_probe` cluster below is split but shares one story —
#: the stale-in-memory detector, found by scitex-dev, 2026-07-12:
#:
#: A long-lived process holds module objects in ``sys.modules``. Upgrading the
#: files on disk does NOT touch them — so a server can serve stale code while
#: its disk, its .dist-info, and this probe all report a current install, each
#: of them truthfully, all of them answering the wrong question.
#:
#: ``features`` is the ONLY check that sees through this, because ``hasattr``
#: reads the LOADED module. Here: the source on disk gains a symbol AFTER the
#: module is already imported, and the feature probe correctly reports it ABSENT
#: — exactly as it would in a server running pre-upgrade code. The remedy is a
#: RESTART, not an upgrade.


def test_disk_level_signals_say_the_install_is_fine(post_upgrade_probe):
    # Arrange
    p = post_upgrade_probe
    # Act
    trustworthy = p.trustworthy
    # Assert — every DISK-level signal says the install is fine...
    assert trustworthy is True


def test_disk_level_code_version_is_current(post_upgrade_probe):
    # Arrange
    p = post_upgrade_probe
    # Act
    code_version = p.code_version
    # Assert
    assert code_version == "2.0.0"


def test_features_interrogate_the_LOADED_module_not_the_disk(post_upgrade_probe):
    # Arrange
    p = post_upgrade_probe
    # Act
    post_upgrade = p.features["post_upgrade"]
    # Assert — ...but the PROCESS does not have the new code, and only the
    # symbol probe can tell us.
    assert post_upgrade is False


# --------------------------------------------------------------------------
# THE PROBE'S OWN BLIND SPOT — found by the probe FAILING on a live install.
# --------------------------------------------------------------------------

#: WHY the `ambiguous_probe` cluster below is split but shares one story — the
#: bug this probe SHIPPED with, and which it failed to catch on itself:
#:
#: A wheel install "usually" means the metadata arrived with the code beside it
#: — so the original code set trustworthy=True and code_version=<metadata>. With
#: TWO dist-infos that is a COIN TOSS: importlib.metadata returns whichever it
#: finds first, which on the live box was the 0.7.50 FOSSIL while the code was
#: 0.9.0. The probe reported trustworthy=True and code_version=0.7.50. Both
#: wrong. It blessed a lying install — the precise failure it exists to prevent.


def test_two_distinfos_still_classify_as_a_wheel(ambiguous_probe):
    # Arrange
    p = ambiguous_probe
    # Act
    kind = p.kind
    # Assert
    assert kind == KIND_WHEEL


def test_two_distinfos_make_the_version_UNTRUSTWORTHY(ambiguous_probe):
    # Arrange
    p = ambiguous_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is False, "an AMBIGUOUS version must never be trusted"


def test_two_distinfos_refuse_to_guess_a_code_version(ambiguous_probe):
    """ "I cannot tell which is real" must not render as a confident number."""
    # Arrange
    p = ambiguous_probe
    # Act
    code_version = p.code_version
    # Assert
    assert code_version is None


def test_two_distinfos_detail_says_ambiguous(ambiguous_probe):
    # Arrange
    p = ambiguous_probe
    # Act
    detail = p.detail
    # Assert
    assert "AMBIGUOUS" in detail


def test_two_distinfos_name_both_and_the_repair(ambiguous_probe):
    # Arrange
    p = ambiguous_probe
    # Act
    detail = p.detail
    # Assert — the reader must be told WHICH two versions are in play.
    assert "0.9.0" in detail and "0.7.50" in detail


def test_two_distinfos_hint_names_the_repair(ambiguous_probe):
    # Arrange
    p = ambiguous_probe
    # Act
    hint = p.hint or ""
    # Assert — names the actual repair.
    assert "rm -rf" in hint


def test_the_symbol_probe_still_tells_the_truth_when_the_version_cannot(
    wheel_pkg_with_two_distinfos, monkeypatch
):
    """The content check is what saved the content checker.

    On the live box, `features` correctly reported the new symbols PRESENT while
    the version string still said 0.7.50. That is the whole argument for symbol
    probing over version reading, demonstrated against the probe's own bug.
    """
    # Arrange
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )
    # Act
    p = probe_install("wheelpkg", features={"new": "wheelpkg:NEW_SYMBOL"})
    # Assert — the CODE is new, whatever the version claims.
    assert p.features["new"] is True


def test_a_single_distinfo_is_still_trustworthy(solo_distinfo_probe):
    """The fix must not make every wheel install suspect — only ambiguous ones."""
    # Arrange
    p = solo_distinfo_probe
    # Act
    trustworthy = p.trustworthy
    # Assert
    assert trustworthy is True


def test_a_single_distinfo_reports_the_code_version(solo_distinfo_probe):
    # Arrange
    p = solo_distinfo_probe
    # Act
    code_version = p.code_version
    # Assert
    assert code_version == "1.0.0"
