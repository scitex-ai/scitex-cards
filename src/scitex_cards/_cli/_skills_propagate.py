#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``skills propagate`` — fleet-wide ``required_skills`` enrichment.

This module is a sibling of :mod:`scitex_cards._cli._skills` and hosts the
manifest loader + propagate verb implementation. It's kept separate so the
core skills group (list / get / install) stays under the project's per-file
line limit while this propagation surface grows.

Purpose
-------
Operator directive — board card
``rec-propagate-scitex-cards-skill-into-every-agent-required-skills``: every
fleet agent's spec.yaml must declare the scitex-cards skill IDs on its skill
list so the agent reads the usage skill on boot and consults the shared YAML
store correctly.

scitex-cards can't directly edit other agents' spec.yaml files
(agent-container's territory). Instead this verb walks a directory of
``<agent>/spec.yaml`` files and idempotently appends the canonical IDs
(from :func:`canonical_skill_ids`) to the configured field.

Default target field is ``metadata.labels.skills`` (v3 spec: CSV string).
``--field spec.required_skills`` switches to the YAML-list flavor (the legacy
name + the operator's wording). Both shapes share set semantics.

Round-trip is via ``ruamel.yaml`` so existing comments + ordering survive
(PR #155 picked the same dep choice for ``mcp install --apply``).
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs


_DEFAULT_SKILL_FIELD = "metadata.labels.skills"


# --------------------------------------------------------------------------- #
# Manifest helpers — shared with the `skills manifest` introspection verb.    #
# --------------------------------------------------------------------------- #


def manifest_path():
    """Resolve the bundled ``_skills/manifest.yaml`` path.

    Sibling of the ``_skills/scitex-cards/`` skill pack so the spec-generation
    script in agent-container can read it without depending on the
    scitex-cards Python API (a plain ``yaml.safe_load`` on this path works).
    """
    from pathlib import Path

    import scitex_cards

    return Path(scitex_cards.__file__).parent / "_skills" / "manifest.yaml"


def load_manifest():
    """Return the parsed manifest as a plain ``dict``.

    Uses ``yaml.safe_load`` — pyyaml is a hard dep.
    """
    import yaml

    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"scitex-cards manifest missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"manifest root must be a mapping, got {type(data).__name__}"
        )
    return data


def canonical_skill_ids():
    """Return the canonical scitex-cards skill IDs (list[str]) from the manifest.

    This is the value that should be appended to every fleet agent's skill
    list. Ordered (manifest order is significant); de-dup is the caller's
    job (the propagate verb handles it).
    """
    data = load_manifest()
    skills = data.get("skills") or []
    if not isinstance(skills, list):
        raise ValueError("manifest.skills must be a list")
    ids = []
    for entry in skills:
        if isinstance(entry, dict) and "id" in entry:
            ids.append(str(entry["id"]))
    return ids


# --------------------------------------------------------------------------- #
# Propagate helpers.                                                          #
# --------------------------------------------------------------------------- #


def _iter_spec_files(agents_dir):
    """Yield every ``spec.yaml`` under ``agents_dir`` (one per agent subdir).

    ``agents_dir/<agent-name>/spec.yaml`` is the v3 dir-as-SSoT layout.
    Returns sorted absolute Paths for stable ordering.
    """
    from pathlib import Path

    root = Path(agents_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/spec.yaml") if p.is_file())


def _split_csv(value):
    """Split a CSV string into a clean list (strip + drop empties)."""
    if not value:
        return []
    return [tok.strip() for tok in str(value).split(",") if tok.strip()]


def _join_csv(items):
    """Join a list back into the canonical ``a, b, c`` shape (single space)."""
    return ", ".join(items)


def _set_nested(doc, dotted_path, value):
    """Set ``doc[a][b][c] = value`` for ``dotted_path == 'a.b.c'``.

    Creates intermediate mappings as needed.
    """
    keys = dotted_path.split(".")
    cur = doc
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def _get_nested(doc, dotted_path):
    """Return ``doc[a][b][c]`` or ``None`` if any segment missing."""
    cur = doc
    for k in dotted_path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def propagate_one(spec_path, canonical_ids, field, write):
    """Append ``canonical_ids`` to the skill list at ``field`` in ``spec_path``.

    Returns a dict with the per-file outcome (used by the CLI for reporting
    + by tests for assertions). The CSV vs list flavor is keyed off the
    field name (``metadata.labels.skills`` is always CSV by v3 convention;
    everything else is treated as a YAML list).

    When ``write`` is False the file is not mutated (dry-run).
    """
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    with spec_path.open("r", encoding="utf-8") as fh:
        doc = yaml.load(fh)
    if doc is None:
        doc = {}

    is_csv = field == "metadata.labels.skills"
    existing_raw = _get_nested(doc, field)

    if is_csv:
        existing = _split_csv(existing_raw)
    else:
        existing = list(existing_raw) if isinstance(existing_raw, list) else []

    # De-dup append in stable order: keep existing order, then append any
    # canonical IDs that aren't already there.
    merged = list(existing)
    added = []
    seen = set(merged)
    for skill_id in canonical_ids:
        if skill_id not in seen:
            merged.append(skill_id)
            added.append(skill_id)
            seen.add(skill_id)

    changed = bool(added)
    if changed and write:
        _set_nested(doc, field, _join_csv(merged) if is_csv else merged)
        with spec_path.open("w", encoding="utf-8") as fh:
            yaml.dump(doc, fh)

    return {
        "path": str(spec_path),
        "field": field,
        "before": list(existing),
        "after": merged,
        "added": added,
        "changed": changed,
    }


# --------------------------------------------------------------------------- #
# Click command builders — attached by `_skills.register()`.                  #
# --------------------------------------------------------------------------- #


def build_manifest_cmd():
    """Return the ``skills manifest`` Click command."""

    @click.command(
        "manifest",
        **spec_command_kwargs(
            summary="Print the canonical skill-ID manifest.",
            description=(
                "The skills that should appear in every fleet agent's "
                "required_skills list, per _skills/manifest.yaml.",
            ),
            examples=(("{prog} skills manifest --json", "Machine-readable manifest."),),
        ),
    )
    @click.option(
        "--json", "as_json", is_flag=True, help="Emit machine-readable JSON."
    )
    def manifest_cmd(as_json: bool) -> None:
        path = manifest_path()
        data = load_manifest()
        if as_json:
            click.echo(json.dumps({"path": str(path), "manifest": data}))
            return
        click.echo(f"# {path}")
        ids = canonical_skill_ids()
        click.echo(f"# canonical skill IDs ({len(ids)}): {', '.join(ids)}")
        for entry in data.get("skills") or []:
            if not isinstance(entry, dict):
                continue
            click.echo(f"- {entry.get('id', '?'):<24} {entry.get('leaf', '')}")

    return manifest_cmd


def build_propagate_cmd():
    """Return the ``skills propagate`` Click command."""

    @click.command(
        "propagate",
        **spec_command_kwargs(
            summary="Append canonical scitex-cards skill IDs to every agent's spec.yaml.",
            description=(
                "Fleet-wide required_skills propagation. Default field "
                "is `metadata.labels.skills` (v3 CSV) — use --field to "
                "target the YAML-list flavor (`spec.required_skills`). "
                "Idempotent — repeated runs are a no-op.",
            ),
            examples=(
                (
                    "{prog} skills propagate "
                    "--agents-dir ~/.scitex/agent-container/agents --dry-run",
                    "Preview the planned edits.",
                ),
                (
                    "{prog} skills propagate "
                    "--agents-dir ~/.scitex/agent-container/agents -y",
                    "Apply the propagation.",
        ),
            ),
        ),
    )
    @click.option(
        "--agents-dir",
        type=click.Path(),
        required=True,
        help="Directory with per-agent subdirs (each holds a spec.yaml).",
    )
    @click.option(
        "--field",
        default=_DEFAULT_SKILL_FIELD,
        show_default=True,
        help=(
            "Dotted path of the skill-list field. CSV shape for "
            "``metadata.labels.skills``; YAML list otherwise."
        ),
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print planned edits without touching disk. SciTeX §2 audit.",
    )
    @click.option(
        "-y",
        "--yes",
        "assume_yes",
        is_flag=True,
        help="Skip confirmation. Required when not --dry-run.",
    )
    @click.option(
        "--json", "as_json", is_flag=True,
        help="Emit per-file outcomes as JSON.",
    )
    def propagate_cmd(
        agents_dir: str,
        field: str,
        dry_run: bool,
        assume_yes: bool,
        as_json: bool,
    ) -> None:
        import sys as _sys
        from pathlib import Path

        canonical = canonical_skill_ids()
        if not canonical:
            raise click.ClickException(
                "scitex-cards manifest has no skills — refusing to "
                "propagate nothing."
            )

        root = Path(agents_dir).expanduser()
        if not root.is_dir():
            raise click.ClickException(f"agents-dir does not exist: {root}")

        spec_files = _iter_spec_files(root)
        if not spec_files:
            click.echo(f"# no spec.yaml files found under {root}", err=True)
            if as_json:
                click.echo(
                    json.dumps({"results": [], "canonical": canonical})
                )
            return

        if not dry_run and not assume_yes and _sys.stdin.isatty():
            raise click.ClickException(
                "`skills propagate` mutates spec.yaml files. Pass -y / "
                "--yes to confirm, or --dry-run to preview."
            )

        results = []
        for spec_path in spec_files:
            try:
                outcome = propagate_one(
                    spec_path, canonical, field, write=not dry_run
                )
            except Exception as exc:  # noqa: BLE001 — surface path
                outcome = {
                    "path": str(spec_path),
                    "field": field,
                    "error": str(exc),
                    "changed": False,
                }
            results.append(outcome)

        if as_json:
            click.echo(
                json.dumps({"results": results, "canonical": canonical})
            )
            return

        changed = sum(1 for r in results if r.get("changed"))
        noop = sum(
            1 for r in results if not r.get("changed") and "error" not in r
        )
        errors = sum(1 for r in results if "error" in r)
        suffix = " (DRY-RUN — no disk changes)" if dry_run else ""
        click.echo(
            f"# propagate{suffix}: {len(results)} spec(s) — "
            f"{changed} updated, {noop} noop, {errors} errored "
            f"(field={field}, canonical={canonical})"
        )
        for r in results:
            if "error" in r:
                click.echo(f"  ! {r['path']}  ERROR: {r['error']}")
            elif r["changed"]:
                click.echo(f"  + {r['path']}  added={r['added']}")
            else:
                click.echo(f"  = {r['path']}  noop")

    return propagate_cmd


# EOF
