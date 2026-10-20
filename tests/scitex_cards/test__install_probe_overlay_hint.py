#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_honest fossil hint is overlay-aware (no in-place reinstall in containers).

AAA-marker regression cover for cards-install-honest-overlay-hint-20261019.

ARRANGE: a synthetic fossil — metadata claims 0.53.8 while the source tree
declares 0.56.1 (the exact drift measured on the live container 2026-10-19).
ACT: probe the fossil twice — once as a bare host, once as an overlay.
ASSERT: exactly one assertion per test (STX-TQ002/TQ007).
"""

from __future__ import annotations

import textwrap

from scitex_cards._install_probe import check_install_honest, probe_install


def _stage_fossil_tree(root, monkeypatch, *, metadata_version, code_version):
    """ARRANGE: editable-shaped tree + metadata claim; return (read_version, dist)."""
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "fossilpkg"
            version = "{code_version}"
            """
        ).strip(),
        encoding="utf-8",
    )
    pkg_dir = root / "src" / "fossilpkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("MARKER = True\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(root / "src"))

    def _claim(dist):
        assert dist == "fossilpkg"
        return metadata_version

    return _claim


def test__fossil_hint_on_bare_host_prescribes_refresh(tmp_path, monkeypatch):
    # ARRANGE: fossil tree, bare host (no overlay).
    read_version = _stage_fossil_tree(
        tmp_path, monkeypatch, metadata_version="0.53.8", code_version="0.56.1"
    )
    # ACT: probe as a bare host.
    probe = probe_install(
        "fossilpkg", read_version=read_version, is_overlay=lambda: False
    )
    # ASSERT: the bare-host remedy (metadata refresh) is still prescribed.
    assert "uv pip install -e" in (probe.hint or "")


def test__fossil_hint_in_overlay_prescribes_rebake_not_reinstall(
    tmp_path, monkeypatch
):
    # ARRANGE: fossil tree, layered container overlay.
    read_version = _stage_fossil_tree(
        tmp_path, monkeypatch, metadata_version="0.53.8", code_version="0.56.1"
    )
    # ACT: probe as an overlay.
    probe = probe_install(
        "fossilpkg", read_version=read_version, is_overlay=lambda: True
    )
    # ASSERT: the overlay remedy names the base-image rebake, not an install.
    assert "REBAKE" in (probe.hint or "")


def test__fossil_hint_in_overlay_names_no_install_command(tmp_path, monkeypatch):
    # ARRANGE: fossil tree, layered container overlay.
    read_version = _stage_fossil_tree(
        tmp_path, monkeypatch, metadata_version="0.53.8", code_version="0.56.1"
    )
    # ACT: probe as an overlay.
    probe = probe_install(
        "fossilpkg", read_version=read_version, is_overlay=lambda: True
    )
    # ASSERT: no in-place install command may appear in the overlay hint.
    assert "uv pip install -e" not in (probe.hint or "")


def test__check_install_honest_forwards_overlay_seam(tmp_path, monkeypatch):
    # ARRANGE: fossil tree, layered container overlay.
    read_version = _stage_fossil_tree(
        tmp_path, monkeypatch, metadata_version="0.53.8", code_version="0.56.1"
    )
    # ACT: run the health-doctor entry point with the overlay seam set.
    result = check_install_honest(
        "fossilpkg", read_version=read_version, is_overlay=lambda: True
    )
    # ASSERT: the doctor still reports untrustworthy, via the overlay hint.
    assert result["ok"] is False and "REBAKE" in str(result["hint"])
