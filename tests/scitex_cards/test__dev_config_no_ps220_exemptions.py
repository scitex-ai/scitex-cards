#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the PS-220 config leg of the audit repair.

``audit-project`` failed on develop HEAD with two PS-220 errors (E3+E4 of
the audit card): ``src/scitex_cards/_git_link.py`` and
``src/scitex_cards/_output.py`` printed protocol payloads with builtin
``print``. Under scitex-dev >= 0.62 PS-220 has no configuration or comment
bypass, so per-site exemptions cannot suppress those findings — the repair
(staged, not yet merged) converts both sites to the prescribed
``scitex_logging`` PlainConsole transport via ``write_content()`` and
records the removal as a do-not-readd note in ``.scitex/dev/config.yaml``
instead of adding exemption entries that would suppress nothing.

These pins hold that end-state without touching a database (plain YAML
parse plus text scans — they run in containers where the store-backed
suite refuses via the fail-LOUD guard). They mirror the exemption shape
(``audit.exemptions``, {rule: [{path, line, reason}]}) without importing
the auditor's privates, so the pins read as contract, not implementation
coupling.

Card ``cards-audit-4-develop-inherited-errors-ps220-ps234-20261012``
owns the repair; E3/E4 (stdout transport) are pinned separately by
``test__output_content_contract.py`` and E1/E2 (runtime ignore) by
``test__runtime_gitignore_policy.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import scitex_cards
import yaml

#: The rule with no configuration bypass: any entry for it in the
#: exemptions map is dead weight that suppresses nothing.
_PS220 = "PS-220"


def _repo_root() -> Path:
    # Arrange-helper: the src-layout checkout holding the package under test.
    return Path(scitex_cards.__file__).resolve().parents[2]


def _config_path() -> Path:
    # Arrange-helper: the repo-owned audit config this repair governs.
    return _repo_root() / ".scitex" / "dev" / "config.yaml"


def _load_config() -> Any:
    # Arrange-helper: the parsed worktree config (read-only).
    return yaml.safe_load(_config_path().read_text(encoding="utf-8"))


def _exemptions_of(config: Any) -> Any:
    # Arrange-helper: the exemptions node, whatever shape it takes.
    if not isinstance(config, dict):
        return None
    audit = config.get("audit")
    if not isinstance(audit, dict):
        return None
    return audit.get("exemptions")


def _ps220_sites(exemptions: Any) -> list[str]:
    # Mirror of the exemption shape: a mapping of rule -> site list, with
    # the list form ({rule: ...} entries) also covered so a re-added entry
    # cannot hide in the other spelling.
    sites: list[str] = []
    if isinstance(exemptions, dict):
        for rule, entries in exemptions.items():
            if rule == _PS220:
                sites.append(f"{rule}: {(entries or [])!r}"[:120])
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("rule") == _PS220:
                        sites.append(f"{rule}[].rule={_PS220}")
                    if isinstance(entry, dict) and entry.get("code") == _PS220:
                        sites.append(f"{rule}[].code={_PS220}")
    elif isinstance(exemptions, list):
        for entry in exemptions:
            if isinstance(entry, dict) and entry.get("rule") == _PS220:
                sites.append(f"[].rule={_PS220}")
    return sites


def test_config_parses_as_yaml_mapping():
    # The config must stay machine-readable: a broken YAML edit fails the
    # audit loader before any rule runs.
    # Arrange + Act
    config = _load_config()
    # Assert
    assert isinstance(config, dict)


def test_no_ps220_key_in_audit_exemptions():
    # PS-220 offers no configuration bypass, so an exemption entry for it
    # suppresses nothing — the map must not carry the key.
    # Arrange
    sites = _ps220_sites(_exemptions_of(_load_config()))
    # Act + Assert
    assert sites == []


def test_exemptions_map_stays_nonempty():
    # Non-vacuity anchor: the live PS-231 entries must still be there, so
    # the pin above is passing on a governed map, not a missing one.
    # Arrange
    exemptions = _exemptions_of(_load_config())
    # Act + Assert
    assert isinstance(exemptions, dict)
    assert "PS-231" in exemptions
    assert exemptions["PS-231"]


def test_do_not_readd_note_names_ps220():
    # The removal note records WHY no entry exists (no bypass under
    # scitex-dev >= 0.62 + the PlainConsole transport), so a future fixer
    # does not re-derive the dead exemption.
    # Arrange
    text = _config_path().read_text(encoding="utf-8")
    # Act + Assert
    assert "Do NOT re-add PS-220 entries here" in text


def test_detector_flags_a_ps220_mapping_key():
    # Non-vacuity: the detector fails on the mapping shape a re-added
    # entry would take.
    # Arrange
    exemptions = {_PS220: [{"path": "src/x.py", "line": 1, "reason": "n/a"}]}
    # Act + Assert
    assert _ps220_sites(exemptions) != []


def test_detector_flags_a_ps220_rule_entry():
    # Non-vacuity: the detector fails on the list-entry spelling too.
    # Arrange
    exemptions = {"PS-231": [{"path": "src/x.py", "line": 1, "rule": _PS220}]}
    # Act + Assert
    assert _ps220_sites(exemptions) != []


def test_detector_accepts_ps231_only():
    # Arrange
    exemptions = {"PS-231": [{"path": "w.yml", "line": 0, "reason": "live"}]}
    # Act + Assert
    assert _ps220_sites(exemptions) == []


def test_config_carries_no_sac_or_harness_coupling():
    # The audit repair is Cards-owned: the config must not name the agent
    # container or any model harness.
    # Arrange
    text = _config_path().read_text(encoding="utf-8").lower()
    # Act + Assert
    assert "sac" not in text
    assert "harness" not in text
