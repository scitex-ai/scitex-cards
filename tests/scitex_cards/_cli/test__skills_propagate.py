#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex-cards skills manifest`` + ``skills propagate``.

The propagate verb is the fleet-wide ``required_skills`` enrichment surface
(board card ``rec-propagate-scitex-cards-skill-into-every-agent-required-
skills``). Tests use Click's ``CliRunner`` against a tmp tree of
spec.yaml files (no mocks — STX-NM / PA-306). One assertion per test
(TQ002 / TQ007); AAA pattern.
"""

from __future__ import annotations

import textwrap

import yaml
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills_propagate import (
    canonical_skill_ids,
    load_manifest,
    manifest_path,
)


# === manifest loader ========================================================


def test_manifest_file_exists_in_package():
    # Arrange
    # Act
    path = manifest_path()
    # Assert
    assert path.is_file()


def test_manifest_loads_as_dict():
    # Arrange
    # Act
    data = load_manifest()
    # Assert
    assert isinstance(data, dict)


def test_manifest_lists_at_least_one_canonical_skill_id():
    # Arrange
    # Act
    ids = canonical_skill_ids()
    # Assert
    assert len(ids) >= 1


def test_manifest_includes_scitex_cards_skill_id():
    # Arrange
    # Act
    ids = canonical_skill_ids()
    # Assert
    assert "scitex-cards" in ids


# === `skills manifest` CLI ==================================================


def test_skills_manifest_cli_exit_code_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "manifest"])
    # Assert
    assert result.exit_code == 0, result.output


def test_skills_manifest_json_includes_scitex_cards():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "manifest", "--json"])
    # Assert
    assert "scitex-cards" in result.output


# === helpers ===============================================================


def _write_spec(dirpath, name, body):
    """Drop ``<dirpath>/<name>/spec.yaml`` with the given body."""
    agent_dir = dirpath / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "spec.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return agent_dir / "spec.yaml"


def _read_yaml(path):
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_MINIMAL_SPEC = """\
apiVersion: scitex-agent-container/v3
kind: Agent
metadata:
  labels:
    role: worker
    skills: scitex-dev, git
spec:
  runtime: apptainer
"""

_SPEC_NO_SKILLS = """\
apiVersion: scitex-agent-container/v3
kind: Agent
metadata:
  labels:
    role: worker
spec:
  runtime: apptainer
"""


# === `skills propagate` --dry-run ===========================================


def test_propagate_dry_run_exit_code_zero(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    _write_spec(agents, "a1", _MINIMAL_SPEC)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["skills", "propagate", "--agents-dir", str(agents), "--dry-run"],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_propagate_dry_run_does_not_mutate_spec(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _MINIMAL_SPEC)
    before = spec.read_text(encoding="utf-8")
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        ["skills", "propagate", "--agents-dir", str(agents), "--dry-run"],
    )
    # Assert
    assert spec.read_text(encoding="utf-8") == before


# === `skills propagate` -y appends ==========================================


def test_propagate_appends_canonical_id_to_csv_field(tmp_path):
    # Arrange — start with `skills: scitex-dev, git` (no scitex-cards).
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _MINIMAL_SPEC)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    # Assert
    csv = _read_yaml(spec)["metadata"]["labels"]["skills"]
    assert "scitex-cards" in [tok.strip() for tok in csv.split(",")]


def test_propagate_preserves_existing_csv_entries(tmp_path):
    # Arrange — existing `scitex-dev` + `git` must survive.
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _MINIMAL_SPEC)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    # Assert
    csv = _read_yaml(spec)["metadata"]["labels"]["skills"]
    tokens = [tok.strip() for tok in csv.split(",")]
    assert "scitex-dev" in tokens and "git" in tokens


def test_propagate_creates_field_when_absent(tmp_path):
    # Arrange — spec has no `metadata.labels.skills` field at all.
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _SPEC_NO_SKILLS)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    # Assert
    csv = _read_yaml(spec)["metadata"]["labels"]["skills"]
    assert "scitex-cards" in csv


# === idempotence ============================================================


def test_propagate_twice_is_idempotent(tmp_path):
    # Arrange
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _MINIMAL_SPEC)
    runner = CliRunner()
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    after_first = spec.read_text(encoding="utf-8")
    # Act
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    # Assert
    assert spec.read_text(encoding="utf-8") == after_first


# === multi-agent fan-out ====================================================


def test_propagate_touches_every_agent_in_dir(tmp_path):
    # Arrange — three agent dirs, all initially without scitex-cards.
    agents = tmp_path / "agents"
    s1 = _write_spec(agents, "a1", _MINIMAL_SPEC)
    s2 = _write_spec(agents, "a2", _MINIMAL_SPEC)
    s3 = _write_spec(agents, "a3", _SPEC_NO_SKILLS)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["skills", "propagate", "--agents-dir", str(agents), "-y"])
    # Assert — every spec mentions scitex-cards afterwards.
    csvs = [_read_yaml(s)["metadata"]["labels"]["skills"] for s in (s1, s2, s3)]
    assert all("scitex-cards" in csv for csv in csvs)


# === field override ========================================================


_SPEC_REQUIRED_SKILLS_LIST = """\
apiVersion: scitex-agent-container/v3
kind: Agent
metadata:
  labels:
    role: worker
spec:
  runtime: apptainer
  required_skills:
    - scitex-dev
    - git
"""


def test_propagate_field_override_writes_yaml_list(tmp_path):
    # Arrange — exercise the YAML-list flavor at `spec.required_skills`.
    agents = tmp_path / "agents"
    spec = _write_spec(agents, "a1", _SPEC_REQUIRED_SKILLS_LIST)
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        [
            "skills",
            "propagate",
            "--agents-dir",
            str(agents),
            "--field",
            "spec.required_skills",
            "-y",
        ],
    )
    # Assert
    rs = _read_yaml(spec)["spec"]["required_skills"]
    assert "scitex-cards" in rs


# === error paths ===========================================================


def test_propagate_missing_agents_dir_exits_nonzero(tmp_path):
    # Arrange — point at a non-existent path.
    missing = tmp_path / "does-not-exist"
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["skills", "propagate", "--agents-dir", str(missing), "-y"],
    )
    # Assert
    assert result.exit_code != 0


def test_propagate_empty_dir_prints_no_specs_marker(tmp_path):
    # Arrange — directory exists but has no spec.yaml children.
    agents = tmp_path / "agents"
    agents.mkdir()
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["skills", "propagate", "--agents-dir", str(agents), "-y"],
    )
    # Assert
    assert "no spec.yaml" in (result.output + (result.stderr or ""))


# EOF
