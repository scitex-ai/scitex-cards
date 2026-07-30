#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the GitHub CI status adapter (no mocks — STX-NM/PA-306).

Covers BOTH the config loader and the gh-CLI adapter:

- ``FleetAdapterError`` is the documented exception class for the whole
  fleet adapter family (the harness contract callers and tests pin on).
- Config loader: missing file = empty repos list (NOT raise — "no repos
  configured" is a valid steady state for a fresh install). Malformed
  YAML RAISES. Env override
  ``SCITEX_TODO_FLEET_CI_REPOS=a,b`` replaces the file-sourced list.
- gh adapter: calling ``fetch_repo_ci_status`` on a non-existent slug
  RAISES (skipped cleanly if the test runner has no ``gh`` auth — we
  refuse to hardcode test-only credentials).

No mocks AND no ``monkeypatch`` — PA-306 forbids the latter (audit
treats pytest's monkeypatch as a mock). Env / cwd manipulation goes
through the suite's :func:`env` fixture
(see ``tests/scitex_cards/conftest.py``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scitex_cards._django.handlers.fleet import (
    FleetAdapterError,
    fetch_repo_ci_status,
    fleet_config_load,
)
from scitex_cards._django.handlers.fleet import _config as fleet_config_mod

# ─── FleetAdapterError shape ────────────────────────────────────────────


def test_fleet_adapter_error_is_runtime_error_subclass_issubclass() -> None:
    """The adapter family's failure class is a ``RuntimeError`` subclass
    so callers that ``except RuntimeError`` (e.g. broad Django error
    middleware) keep working — but a dedicated subclass lets the view
    + tests pin behavior with ``pytest.raises(FleetAdapterError)``."""
    # Arrange
    # Act
    # Assert
    assert issubclass(FleetAdapterError, RuntimeError)


def test_fleet_adapter_error_is_runtime_error_subclass_fleetadaptererror() -> None:
    """The adapter family's failure class is a ``RuntimeError`` subclass
    so callers that ``except RuntimeError`` (e.g. broad Django error
    middleware) keep working — but a dedicated subclass lets the view
    + tests pin behavior with ``pytest.raises(FleetAdapterError)``."""
    # Arrange
    # Act
    # Assert
    assert FleetAdapterError is not RuntimeError


# ─── Config loader ──────────────────────────────────────────────────────


def _isolate_home(env, tmp_path):
    """Point ``Path.home()`` at ``tmp_path`` and clear the env override.

    Each test owns its own HOME so we never read the operator's real
    ``~/.scitex/cards/dashboard.json``. Uses the PA-306-compliant ``env``
    helper from the shared conftest (NOT monkeypatch).
    """
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")


def test_config_missing_file_returns_empty_repos(env, tmp_path) -> None:
    """A fresh install has no ``dashboard.json`` and that is NOT an
    error — "no repos configured" is a valid steady state and the UI
    hides the pills strip gracefully."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Act
    out = fleet_config_load()
    # Assert
    assert out == {"fleet": {"ci_status": {"repos": []}}}


def test_config_malformed_yaml_raises_raises_fleetadaptererror(env, tmp_path) -> None:
    """A broken config IS fail-loud so the operator does not stare at
    an empty strip wondering why their config is being ignored."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    # Unterminated mapping — JSON parser bails.
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": [a, b,',
        encoding="utf-8",
    )
    # Act
    # Assert
    # The message must name the file path so the operator can find it.
    with pytest.raises(FleetAdapterError, match="dashboard.json"):
        fleet_config_load()


def test_config_env_override_replaces_file_list(env, tmp_path) -> None:
    """The env var trumps the file — handy for tests AND for the
    operator to flip the set without editing the file."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": ["file/one", "file/two"]}}}',
        encoding="utf-8",
    )
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "env/aaa, env/bbb")
    # Act
    out = fleet_config_load()
    # Assert
    assert out["fleet"]["ci_status"]["repos"] == ["env/aaa", "env/bbb"]


def test_config_env_override_without_file(env, tmp_path) -> None:
    """The env override alone is enough — no file required."""
    # Arrange
    _isolate_home(env, tmp_path)
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "owner/x,owner/y")
    # Act
    out = fleet_config_load()
    # Assert
    assert out["fleet"]["ci_status"]["repos"] == ["owner/x", "owner/y"]


def test_config_env_override_empty_string_means_no_repos(env, tmp_path) -> None:
    """Empty env override yields an empty list (not a one-item ``""``)
    — the operator can intentionally disable the strip with an empty
    env value."""
    # Arrange
    _isolate_home(env, tmp_path)
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "")
    # Act
    out = fleet_config_load()
    # Assert
    assert out["fleet"]["ci_status"]["repos"] == []


def test_config_reads_file_when_env_unset(env, tmp_path) -> None:
    """File-sourced list shines through when the env override is unset."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": ["file/one", "file/two"]}}}',
        encoding="utf-8",
    )
    # Act
    out = fleet_config_load()
    # Assert
    assert out["fleet"]["ci_status"]["repos"] == ["file/one", "file/two"]


# ─── gh adapter ─────────────────────────────────────────────────────────


def _gh_authed() -> bool:
    """True iff ``gh`` is on PATH AND ``gh auth status`` succeeds.

    The adapter test refuses to hardcode test-only credentials, so it
    self-skips on a CI machine without auth.
    """
    exe = shutil.which("gh")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "auth", "status"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_AUTHED = _gh_authed()


@pytest.mark.skipif(not _AUTHED, reason="gh CLI not installed or not authenticated")
def test_nonexistent_repo_raises() -> None:
    """Calling the adapter on a slug that does not exist on GitHub
    must surface as ``FleetAdapterError`` — the fail-loud contract."""
    # Arrange
    # Act
    # Assert
    with pytest.raises(FleetAdapterError):
        # A slug that almost-certainly resolves to 404. We avoid using a
        # name that COULD be claimed later by namespacing it under a
        # uuid-like path.
        fetch_repo_ci_status("ywatanabe1989/scitex-todo-test-does-not-exist-xyz123")


def test_invalid_slug_shape_raises_without_gh_raises_fleetadaptererror() -> None:
    """Slug validation happens before we touch ``gh``, so this test
    is gh-independent — it pins the input contract on every CI box."""
    # Arrange
    # Act
    # Assert
    with pytest.raises(FleetAdapterError, match="owner/name"):
        fetch_repo_ci_status("not-a-slug")


def test_gh_missing_binary_raises_raises_fleetadaptererror(env) -> None:
    """Surfacing "gh not installed" must NOT silently fall back to an
    empty-checks success — that would lie to the operator about fleet
    health. Simulate the missing binary by clobbering PATH."""
    # Arrange
    env.set("PATH", "")
    from scitex_cards._django.handlers.fleet import gh_ci as gh_ci_mod

    # Act
    # Assert
    with pytest.raises(FleetAdapterError, match="(?i)gh"):
        gh_ci_mod.fetch_repo_ci_status("owner/name")


# ─── overall-reducer pure unit tests ────────────────────────────────────


@pytest.mark.parametrize(
    "checks,expected",
    [
        ([], "unknown"),
        (
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "success"},
            ],
            "success",
        ),
        (
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "failure"},
            ],
            "failure",
        ),
        (
            [
                {"status": "in_progress", "conclusion": None},
                {"status": "completed", "conclusion": "success"},
            ],
            "pending",
        ),
        (
            [{"status": "completed", "conclusion": "timed_out"}],
            "failure",
        ),
        (
            [
                {"status": "completed", "conclusion": "neutral"},
                {"status": "completed", "conclusion": "skipped"},
            ],
            "success",
        ),
    ],
)
def test_overall_status_reducer_picks_worst(checks, expected) -> None:
    """The reducer is the heart of the pill color — failure beats
    pending beats success. Pin every branch explicitly so the FE color
    mapping stays in lock-step with what the back-end emits."""
    # Arrange
    # Act
    from scitex_cards._django.handlers.fleet.gh_ci import _overall_from_checks

    # Assert
    assert _overall_from_checks(checks) == expected


# ─── module-surface contract ────────────────────────────────────────────


def test_config_module_constant_paths_str() -> None:
    """Lock the config-path constant so a rename downstream forces a
    test update. Operators search for this literal when debugging."""
    # Arrange
    # Act
    # Assert
    assert str(fleet_config_mod._CONFIG_REL) == str(
        Path(".scitex") / "cards" / "dashboard.json"
    )


def test_config_module_constant_paths_env_repos() -> None:
    """Lock the config-path constant so a rename downstream forces a
    test update. Operators search for this literal when debugging."""
    # Arrange
    # Act
    # Assert
    assert fleet_config_mod._ENV_REPOS == "SCITEX_TODO_FLEET_CI_REPOS"


def test_module_has_documented_attrs() -> None:
    """The harness ``__init__`` exports its public API verbatim — pin
    the surface so downstream waves (hosts / mesh / …) can ``from
    scitex_cards._django.handlers.fleet import FleetAdapterError`` and
    expect that name to be stable."""
    # Arrange
    from scitex_cards._django.handlers import fleet as fleet_pkg

    # Act
    # Assert
    for attr in (
        "FleetAdapterError",
        "fleet_config_load",
        "fetch_repo_ci_status",
        "fetch_many_ci_status",
        "fleet_ci_status_view",
    ):
        assert hasattr(fleet_pkg, attr), f"missing public name: {attr}"


# ─── bulk GraphQL adapter — fetch_many_ci_status ────────────────────────


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESS", "success"),
        ("FAILURE", "failure"),
        ("ERROR", "failure"),
        ("PENDING", "pending"),
        ("EXPECTED", "pending"),
        (None, "unknown"),
        ("", "unknown"),
        ("WEIRD", "unknown"),
    ],
)
def test_overall_from_rollup_maps_state_to_color(state, expected) -> None:
    """The GraphQL rollup-state → pill-color mapping mirrors the REST
    reducer (FAILURE/ERROR red, PENDING/EXPECTED amber, SUCCESS green,
    else grey) so both fetch paths feed the FE identical colors."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _overall_from_rollup

    # Act
    result = _overall_from_rollup(state)

    # Assert
    assert result == expected


@pytest.mark.parametrize(
    "slug,is_valid",
    [
        ("owner/name", True),
        ("ywatanabe1989/scitex-todo", True),
        ("dotted.owner/dot.repo-1", True),
        ("no-slash", False),
        ("too/many/slashes", False),
        ("owner/", False),
        ("/name", False),
        ('owner/na"me', False),
        ("owner/na me", False),
    ],
)
def test_split_slug_accepts_only_safe_tokens(slug, is_valid) -> None:
    """A slug splits only when BOTH halves are safe GitHub tokens, so a
    stray config line can never be interpolated into the GraphQL query."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _split_slug

    # Act
    parsed = _split_slug(slug)

    # Assert
    assert (parsed is not None) is is_valid


def test_build_graphql_query_aliases_each_repo() -> None:
    """Each repo becomes its own ``r<i>`` alias so ONE request resolves
    the whole batch; owner + name land as literal string args."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _build_graphql_query

    # Act
    query = _build_graphql_query(["a/b", "c/d"])

    # Assert
    assert all(
        token in query
        for token in (
            'r0: repository(owner: "a", name: "b")',
            'r1: repository(owner: "c", name: "d")',
            "statusCheckRollup",
        )
    )


def test_parse_graphql_repo_null_node_becomes_error_entry() -> None:
    """A null repository node (not found / no access) maps to a per-repo
    error entry — never a silent success."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _parse_graphql_repo

    # Act
    out = _parse_graphql_repo(None, "owner/missing")

    # Assert
    assert "error" in out


def test_parse_graphql_repo_missing_branch_is_unknown() -> None:
    """An empty repo (no default branch) is a legitimate steady state —
    grey/unknown, not an error."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _parse_graphql_repo

    # Act
    out = _parse_graphql_repo({"defaultBranchRef": None}, "owner/empty")

    # Assert
    assert out["overall"] == "unknown"


def test_parse_graphql_repo_full_node_maps_failure() -> None:
    """A fully-resolved node maps the rollup state into the pill overall."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import _parse_graphql_repo

    node = {
        "defaultBranchRef": {
            "name": "main",
            "target": {
                "oid": "deadbeef",
                "statusCheckRollup": {"state": "FAILURE"},
            },
        }
    }

    # Act
    out = _parse_graphql_repo(node, "owner/repo")

    # Assert
    assert out["overall"] == "failure"


def test_fetch_many_invalid_slugs_preserve_order_as_errors() -> None:
    """Malformed slugs short-circuit to error entries WITHOUT touching the
    network, and the result preserves input order + coverage so the FE
    strip stays aligned with the configured list."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import fetch_many_ci_status

    # Act
    out = fetch_many_ci_status(["bad-no-slash", "also/bad/extra"])

    # Assert
    assert [r["slug"] for r in out] == ["bad-no-slash", "also/bad/extra"]


def test_fetch_many_empty_input_returns_empty_list() -> None:
    """No repos configured → empty result and no gh call."""
    # Arrange
    from scitex_cards._django.handlers.fleet.gh_ci import fetch_many_ci_status

    # Act
    out = fetch_many_ci_status([])

    # Assert
    assert out == []


# ─── ecosystem spin-out — fleet_config_load `ecosystem` flag ────────────


def _scitex_dev_available() -> bool:
    """True iff ``scitex-dev`` is on PATH (the ecosystem registry source).
    Mirrors the gh-auth self-skip so CI without scitex-dev stays green."""
    return shutil.which("scitex-dev") is not None


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        (False, False),
        ("false", False),
        ("0", False),
        ("", False),
        (None, False),
    ],
)
def test_truthy_accepts_common_flag_spellings(value, expected) -> None:
    """The ecosystem flag accepts the usual YAML / env truthy spellings."""
    # Arrange
    truthy = fleet_config_mod._truthy

    # Act
    result = truthy(value)

    # Assert
    assert result is expected


def test_ecosystem_flag_off_keeps_only_explicit_repos(env, tmp_path) -> None:
    """Without the flag the watch-list is EXACTLY the explicit repos — the
    ecosystem union is strictly opt-in."""
    # Arrange
    _isolate_home(env, tmp_path)
    env.delete("SCITEX_TODO_FLEET_CI_ECOSYSTEM")
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "owner/x,owner/y")

    # Act
    out = fleet_config_load()

    # Assert
    assert out["fleet"]["ci_status"]["repos"] == ["owner/x", "owner/y"]


@pytest.fixture()
def _unioned_ecosystem_repos(env, tmp_path):
    """Resolve the CI repo list with the ecosystem flag ON.

    The ecosystem is sourced from a LIVE ``scitex-dev ecosystem list``
    subprocess that intermittently returns nothing under suite load;
    when it does, the union can't be exercised, so the fixture SKIPs
    (hermetic guard, same spirit as #218's sac-mesh skip) rather than
    letting the dependent tests flake.
    """
    if not _scitex_dev_available():
        pytest.skip("scitex-dev not on PATH (ecosystem registry source)")
    _isolate_home(env, tmp_path)
    fleet_config_mod._eco_cache["ts"] = 0.0
    fleet_config_mod._eco_cache["repos"] = []
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "owner/pinned")
    env.set("SCITEX_TODO_FLEET_CI_ECOSYSTEM", "1")
    repos = fleet_config_load()["fleet"]["ci_status"]["repos"]
    if len(repos) <= 1:
        pytest.skip("live `scitex-dev ecosystem list` returned no ecosystem repos")
    return repos


def test_ecosystem_flag_keeps_operator_pin_first(_unioned_ecosystem_repos) -> None:
    """With the flag ON the explicit repo still leads the list."""
    # Arrange
    repos = _unioned_ecosystem_repos
    # Act
    # Assert
    assert repos[0] == "owner/pinned"


def test_ecosystem_flag_unions_extra_repos(_unioned_ecosystem_repos) -> None:
    """The live ecosystem registry is unioned in beyond the single pin."""
    # Arrange
    repos = _unioned_ecosystem_repos
    # Act
    # Assert
    assert len(repos) > 1


def test_ecosystem_flag_union_is_deduped(_unioned_ecosystem_repos) -> None:
    """The unioned list carries no duplicates."""
    # Arrange
    repos = _unioned_ecosystem_repos
    # Act
    # Assert
    assert len(repos) == len(set(repos))
