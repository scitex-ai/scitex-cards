"""Real CLI diagnostics, data streams, thresholds and forwarding exits.

The watch-ci cases use an owned config and an empty executable search path:
the actual adapter reports a missing gh binary without contacting GitHub.
No store or provider is needed by these command paths.
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
def test_skill_json_remains_raw_at_shared_error_threshold(tmp_path, level):
    result = _run(
        tmp_path, ["-m", "scitex_cards", "skills", "list", "--json"], level=level
    )
    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)
    assert "02_quick-start" in {entry["name"] for entry in entries}
    assert result.stderr == ""


@pytest.mark.parametrize("as_json", [False, True])
def test_skill_body_remains_verbatim_data(tmp_path, as_json):
    listing = _run(tmp_path, ["-m", "scitex_cards", "skills", "list", "--json"])
    assert listing.returncode == 0, listing.stderr
    entry = next(e for e in json.loads(listing.stdout) if e["name"] == "02_quick-start")
    expected = Path(entry["path"]).read_text(encoding="utf-8")
    args = ["-m", "scitex_cards", "skills", "get", "02_quick-start"]
    if as_json:
        args.append("--json")
    result = _run(tmp_path, args, level="ERROR")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if as_json:
        assert json.loads(result.stdout)["content"] == expected
    else:
        assert result.stdout == expected + "\n"


@pytest.mark.parametrize(
    "level, visible", [("INFO", True), ("ERROR", True), ("CRITICAL", False)]
)
def test_missing_skill_uses_error_stderr_and_preserves_exit(tmp_path, level, visible):
    result = _run(
        tmp_path, ["-m", "scitex_cards", "skills", "get", "missing-skill"], level=level
    )
    assert result.returncode == 1
    assert result.stdout == ""
    if visible:
        assert "ERRO: skill not found: missing-skill" in result.stderr
        assert "ERRO: available:" in result.stderr
        assert "02_quick-start" in result.stderr
    else:
        assert result.stderr == ""


@pytest.mark.parametrize(
    "verb, message",
    [("list", "no skills found at"), ("install", "no skills directory at")],
)
def test_missing_skill_directory_keeps_error_exit_and_no_write(tmp_path, verb, message):
    missing = tmp_path / "missing-skills"
    dest = tmp_path / "install-destination"
    script = """
import sys
from scitex_cards._cli import main, _skills
_skills._SKILLS_PKG = _skills._SKILLS_PKG_LEGACY = sys.argv[1]
main(args=["skills", sys.argv[2], *sys.argv[3:]])
"""
    args = ["-c", script, str(missing), verb]
    if verb == "install":
        args += ["--dest", str(dest)]
    result = _run(tmp_path, args, level="ERROR")
    assert result.returncode == 1
    assert result.stdout == ""
    assert f"ERRO: {message} {missing}" in result.stderr
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
def test_ci_config_failure_uses_error_stderr_and_exit_one(tmp_path, level):
    config = tmp_path / "dashboard.json"
    config.write_text("{", encoding="utf-8")
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ERRO: # watch-ci: config load failed:" in result.stderr
    assert "malformed dashboard config" in result.stderr
    assert not (tmp_path / "ci-state.json").exists()


@pytest.mark.parametrize("level, visible", [("INFO", True), ("ERROR", False)])
def test_ci_invalid_slug_and_summary_obey_threshold_without_state_write(
    tmp_path, level, visible
):
    config = tmp_path / "dashboard.json"
    config.write_text(json.dumps({"fleet": {"ci_status": {"repos": ["invalid-slug"]}}}))
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    if visible:
        assert "WARN: # watch-ci: skipping invalid slug 'invalid-slug'" in result.stderr
        assert (
            "INFO: # watch-ci: repos=1 transitions=0 errors=0 dry_run=True"
            in result.stderr
        )
    else:
        assert result.stderr == ""
    assert not (tmp_path / "ci-state.json").exists()


@pytest.mark.parametrize("level", ["INFO", "ERROR"])
def test_ci_real_missing_adapter_retains_error_exit_without_network(tmp_path, level):
    config = tmp_path / "dashboard.json"
    config.write_text(
        json.dumps({"fleet": {"ci_status": {"repos": ["example/missing"]}}})
    )
    result = _run(tmp_path, ["-c", _WATCH, str(config)], level=level, path=tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert (
        "ERRO: # watch-ci: example/missing adapter error: gh CLI not found on PATH"
        in result.stderr
    )
    assert ("INFO: # watch-ci:" in result.stderr) is (level == "INFO")
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


@pytest.mark.parametrize("level, visible", [("WARNING", True), ("ERROR", False)])
def test_fallback_alias_warns_once_and_preserves_target_json(tmp_path, level, visible):
    args = ["-c", _ALIAS, "old-fixture", "--value", "42"]
    first = _run(tmp_path, args, level=level)
    second = _run(tmp_path, args, level=level)
    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout) == json.loads(second.stdout) == {"value": 42}
    assert ("WARN: 'old-fixture' is deprecated" in first.stderr) is visible
    if not visible:
        assert first.stderr == ""
    assert second.stderr == ""


def test_fallback_alias_preserves_target_failure_exit(tmp_path):
    result = _run(tmp_path, ["-c", _ALIAS, "old-fixture", "--value", "42", "--fail"])
    assert result.returncode == 1
    assert result.stdout == ""
    assert "WARN: 'old-fixture' is deprecated" in result.stderr
    assert "Error: target failed" in result.stderr
