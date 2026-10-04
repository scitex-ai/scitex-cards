"""Real CLI diagnostics, data streams, thresholds and forwarding exits.

The watch-ci cases use an owned config and an empty executable search path:
the actual adapter reports a missing gh binary without contacting GitHub.
No store or provider is needed by these command paths.

One assertion per test (STX-TQ007); AAA markers on every case.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(tmp_path, args, *, level="INFO", path=None):
    env = os.environ.copy()
    for name in list(env):
        if (
            name.startswith(
                (
                    "PG",
                    "SCITEX_",
                    "DJANGO_",
                    "OPENAI_",
                    "ANTHROPIC_",
                    "AZURE_",
                    "AWS_",
                    "GOOGLE_",
                    "GEMINI_",
                    "GITHUB_",
                    "GH_",
                    "OLLAMA_",
                    "SAC_",
                )
            )
            or name.endswith(("_TOKEN", "_SECRET", "_API_KEY", "_DSN", "_DATABASE_URL"))
            or name in {"DATABASE_URL", "REDIS_URL"}
        ):
            env.pop(name)
    source = Path(__file__).resolve().parents[2] / "src"
    if source.is_dir():
        env["PYTHONPATH"] = str(source)
    env.update(
        TMPDIR=str(tmp_path),
        XDG_RUNTIME_DIR=str(tmp_path),
        SCITEX_DIR=str(tmp_path / "state"),
        SCITEX_CARDS_CI_STATE=str(tmp_path / "ci-state.json"),
        SCITEX_DEV_CURRENCY_SEVERITY="silent",
        SCITEX_LOGGING_LEVEL=level,
        SCITEX_LOGGING_FORMAT="default",
        SCITEX_LOGGING_FORCE_COLOR="0",
        SCITEX_FORCE_COLOR="0",
    )
    if path is not None:
        env["PATH"] = str(path)
    return subprocess.run(
        [sys.executable, *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_skill_json_list_exits_zero(tmp_path, level):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "list", "--json"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.returncode == 0


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_skill_json_lists_quick_start(tmp_path, level):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "list", "--json"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert "02_quick-start" in {entry["name"] for entry in json.loads(result.stdout)}


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_skill_json_writes_no_stderr(tmp_path, level):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "list", "--json"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.stderr == ""


def _quick_start_expected(tmp_path):
    # Arrange helper (not a test): fetch the stored body for comparison.
    listing = _run(tmp_path, ["-m", "scitex_cards", "skills", "list", "--json"])
    assert listing.returncode == 0, listing.stderr
    entry = next(e for e in json.loads(listing.stdout) if e["name"] == "02_quick-start")
    return Path(entry["path"]).read_text(encoding="utf-8")


def test_skill_body_json_exits_zero(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start", "--json"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.returncode == 0


def test_skill_body_text_exits_zero(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.returncode == 0


def test_skill_body_json_writes_no_stderr(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start", "--json"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.stderr == ""


def test_skill_body_text_writes_no_stderr(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.stderr == ""


def test_skill_body_json_matches_stored_content(tmp_path):
    # Arrange
    expected = _quick_start_expected(tmp_path)
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start", "--json"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert json.loads(result.stdout)["content"] == expected


def test_skill_body_text_matches_stored_content(tmp_path):
    # Arrange
    expected = _quick_start_expected(tmp_path)
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.stdout == expected + "\n"


@pytest.mark.parametrize("level", ["INFO", "ERROR", "CRITICAL"])
def test_missing_skill_preserves_exit_one(tmp_path, level):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.returncode == 1


@pytest.mark.parametrize("level", ["INFO", "ERROR", "CRITICAL"])
def test_missing_skill_writes_no_stdout(tmp_path, level):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.stdout == ""


def test_missing_skill_names_missing_skill(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level="INFO")
    # Assert
    assert "ERRO: skill not found: missing-skill" in result.stderr


def test_missing_skill_lists_available(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level="INFO")
    # Assert
    assert "ERRO: available:" in result.stderr


def test_missing_skill_suggests_quick_start(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert "02_quick-start" in result.stderr


def test_missing_skill_stays_silent_below_threshold(tmp_path):
    # Arrange
    args = ["-m", "scitex_cards", "skills", "get", "missing-skill"]
    # Act
    result = _run(tmp_path, args, level="CRITICAL")
    # Assert
    assert result.stderr == ""


@pytest.mark.parametrize("verb", ["list", "install"])
def test_missing_skill_directory_preserves_exit_one(tmp_path, verb):
    # Arrange
    missing = tmp_path / "missing-skills"
    script = (
        "import sys\n"
        "from scitex_cards._cli import main, _skills\n"
        "_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]\n"
        "main(args=['skills', sys.argv[2], *sys.argv[3:]])\n"
    )
    # Act
    result = _run(tmp_path, ["-c", script, str(missing), verb], level="ERROR")
    # Assert
    assert result.returncode == 1


@pytest.mark.parametrize("verb", ["list", "install"])
def test_missing_skill_directory_writes_no_stdout(tmp_path, verb):
    # Arrange
    missing = tmp_path / "missing-skills"
    script = (
        "import sys\n"
        "from scitex_cards._cli import main, _skills\n"
        "_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]\n"
        "main(args=['skills', sys.argv[2], *sys.argv[3:]])\n"
    )
    # Act
    result = _run(tmp_path, ["-c", script, str(missing), verb], level="ERROR")
    # Assert
    assert result.stdout == ""


def test_missing_skill_directory_list_names_directory(tmp_path):
    # Arrange
    missing = tmp_path / "missing-skills"
    script = (
        "import sys\n"
        "from scitex_cards._cli import main, _skills\n"
        "_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]\n"
        "main(args=['skills', sys.argv[2], *sys.argv[3:]])\n"
    )
    # Act
    result = _run(tmp_path, ["-c", script, str(missing), "list"], level="ERROR")
    # Assert
    assert f"ERRO: no skills found at {missing}" in result.stderr


def test_missing_skill_directory_install_names_directory(tmp_path):
    # Arrange
    missing = tmp_path / "missing-skills"
    dest = tmp_path / "install-destination"
    script = (
        "import sys\n"
        "from scitex_cards._cli import main, _skills\n"
        "_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]\n"
        "main(args=['skills', sys.argv[2], *sys.argv[3:]])\n"
    )
    # Act
    result = _run(
        tmp_path, ["-c", script, str(missing), "install", "--dest", str(dest)], level="ERROR"
    )
    # Assert
    assert f"ERRO: no skills directory at {missing}" in result.stderr


def test_missing_skill_directory_install_writes_nothing(tmp_path):
    # Arrange
    missing = tmp_path / "missing-skills"
    dest = tmp_path / "install-destination"
    script = (
        "import sys\n"
        "from scitex_cards._cli import main, _skills\n"
        "_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]\n"
        "main(args=['skills', sys.argv[2], *sys.argv[3:]])\n"
    )
    # Act
    _run(tmp_path, ["-c", script, str(missing), "install", "--dest", str(dest)], level="ERROR")
    # Assert
    assert not dest.exists()


_WATCH = """
import sys
from pathlib import Path
from scitex_cards._django.handlers.fleet import _config
from scitex_cards._cli._ci_watch import watch_ci_cmd
_config._CONFIG_REL = Path(sys.argv[1])
watch_ci_cmd.main(args=["--once", "--dry-run"], prog_name="scitex-cards watch-ci")
"""


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_config_failure_preserves_exit_one(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.returncode == 1


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_config_failure_writes_no_stdout(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.stdout == ""


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_config_failure_reports_load_failure(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert "ERRO: # watch-ci: config load failed:" in result.stderr


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_config_failure_reports_malformed_config(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert "malformed dashboard config" in result.stderr


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_config_failure_writes_no_state(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    # Act
    _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert not (tmp_path / "ci-state.json").exists()


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_invalid_slug_preserves_success_exit(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.returncode == 0


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_invalid_slug_writes_no_stdout(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.stdout == ""


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_invalid_slug_writes_no_state(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert not (tmp_path / "ci-state.json").exists()


def test_ci_invalid_slug_warns_at_info(tmp_path):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level="INFO", path=tmp_path)
    # Assert
    assert "WARN: # watch-ci: skipping invalid slug 'invalid-slug'" in result.stderr


def test_ci_invalid_slug_summarizes_at_info(tmp_path):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level="INFO", path=tmp_path)
    # Assert
    assert "INFO: # watch-ci: repos=1 transitions=0 errors=0 dry_run=True" in result.stderr


def test_ci_invalid_slug_stays_silent_below_threshold(tmp_path):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level="ERROR", path=tmp_path)
    # Assert
    assert result.stderr == ""


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_missing_adapter_preserves_error_exit(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.returncode == 1


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_missing_adapter_writes_no_stdout(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert result.stdout == ""


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_missing_adapter_reports_absent_gh(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert "ERRO: # watch-ci: example/missing adapter error: gh CLI not found on PATH" in (
        result.stderr
    )


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_missing_adapter_info_line_follows_threshold(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    # Act
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert ("INFO: # watch-ci:" in result.stderr) is (level == "INFO")


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_missing_adapter_writes_no_state(tmp_path, level):
    # Arrange
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    # Act
    _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    # Assert
    assert not (tmp_path / "ci-state.json").exists()


_ALIAS = """
import click
import json
from scitex_cards._cli._compat import _fallback_deprecated_alias
@click.group()
def main():
    pass
@main.command()
@click.option("--value", type=int, required=True)
@click.option("--fail", is_flag=True)
def current(value, fail):
    if fail:
        raise click.ClickException("target failed")
    click.echo(json.dumps({"value": value}))
_fallback_deprecated_alias(main, "old-fixture", target="current", remove_in="0.9")
main()
"""


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_fallback_alias_preserves_target_exit(tmp_path, level):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.returncode == 0


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_fallback_alias_preserves_target_json(tmp_path, level):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert json.loads(result.stdout) == {"value": 42}


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_fallback_alias_warn_presence_follows_threshold(tmp_path, level):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    # Act
    result = _run(tmp_path, args, level=level)
    # Assert
    assert ("WARN: 'old-fixture' is deprecated" in result.stderr) is (level == "WARNING")


def test_fallback_alias_hidden_level_writes_no_stderr(tmp_path):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    # Act
    result = _run(tmp_path, args, level="ERROR")
    # Assert
    assert result.stderr == ""


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_fallback_alias_second_run_writes_no_stderr(tmp_path, level):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    # Act
    _run(tmp_path, args, level=level)
    result = _run(tmp_path, args, level=level)
    # Assert
    assert result.stderr == ""


def test_fallback_alias_preserves_target_failure_exit(tmp_path):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42", "--fail"]
    # Act
    result = _run(tmp_path, args)
    # Assert
    assert result.returncode == 1


def test_fallback_alias_failure_writes_no_stdout(tmp_path):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42", "--fail"]
    # Act
    result = _run(tmp_path, args)
    # Assert
    assert result.stdout == ""


def test_fallback_alias_failure_still_warns(tmp_path):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42", "--fail"]
    # Act
    result = _run(tmp_path, args)
    # Assert
    assert "WARN: 'old-fixture' is deprecated" in result.stderr


def test_fallback_alias_failure_reports_target_error(tmp_path):
    # Arrange
    args = ["-c", _ALIAS, "old-fixture", "--value", "42", "--fail"]
    # Act
    result = _run(tmp_path, args)
    # Assert
    assert "Error: target failed" in result.stderr
