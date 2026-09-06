#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time fold of the LEGACY embedded ``inboxes:`` section into the sidecar.

Originally split out of the per-host inbox module (retired 2026-08-23,
operator ruling — the file-backed rail is eradicated as a fleet-state backend).
What
survives here is the part that outlived that module: pre-cutover stores may
still carry an ``inboxes:`` section embedded in the monolithic legacy
document, and :func:`_migrate_legacy_yaml_once` folds it into the standalone
``inboxes.json`` sidecar exactly once, on first access, for the file-backed
break-glass backend (``SCITEX_CARDS_INBOX_BACKEND=yaml``). The rail-specific
half (the migrate verb, ``gather_migratable_inboxes``, ``info``) was deleted
with the module it fed.
"""

from __future__ import annotations

from pathlib import Path


def _read_legacy_embedded_inboxes(path: Path) -> dict[str, list[dict]]:
    """Read the LEGACY embedded ``inboxes:`` section off the pre-cutover
    monolithic task-store document (absent / malformed -> {}). The one
    remaining caller is :func:`_migrate_legacy_yaml_once`.
    """
    from ._inbox import _INBOXES_KEY

    if not path.exists():
        return {}
    from ._yaml import safe_load

    try:
        with path.open(encoding="utf-8") as handle:
            data = safe_load(handle) or {}
    except (UnicodeDecodeError, ValueError, OSError):
        # THE STORE IS POSTGRESQL NOW, so "the legacy document" may not parse
        # as UTF-8 YAML at all. This function's contract has always been
        # "absent / malformed -> {}"; it just never covered malformed-because-
        # not-yaml.
        #
        # It matters because a FRESH store hits it on its very first inbox
        # access. Returning {} is right, not a papering-over: a store with no
        # embedded `inboxes:` section genuinely has nothing to migrate.
        return {}
    raw = data.get(_INBOXES_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for rid, records in raw.items():
        if not isinstance(rid, str) or not rid:
            continue
        out[rid] = (
            [r for r in records if isinstance(r, dict)]
            if isinstance(records, list)
            else []
        )
    return out


def _migrate_legacy_yaml_once(json_path: Path, legacy_doc_path: Path) -> None:
    """Fold a legacy EMBEDDED ``inboxes:`` section into ``inboxes.json``, once.

    No-op unless ``json_path`` is absent AND the legacy document has data.
    No permanent YAML fallback: once ``inboxes.json`` exists, never fires
    again.
    """
    from ._inbox import _save_inboxes_unlocked

    if json_path.exists():
        return
    raw = _read_legacy_embedded_inboxes(legacy_doc_path)
    if raw:
        _save_inboxes_unlocked(raw, json_path)


__all__ = [
    "_migrate_legacy_yaml_once",
    "_read_legacy_embedded_inboxes",
]

# EOF
