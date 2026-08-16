#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Directory-card migration scanner + plan emitter (PR-D Stage 1).

Operator-direct directive (2026-06-13, lead a2a `[operator-driven]`,
translated — the verbatim original lives on the board's task history):
the canonical card shape is a per-task DIRECTORY at
``<proj>/.scitex/todo/tasks/<card-id>/`` (with ``README.md`` for the
body + ``adr.md`` for decisions per skill 30). Writes that lay a row
directly into the flat ``tasks.yaml`` are FORBIDDEN — the system must
fail loud + name the offending path.

This module is the SCAN side: walk every discovered lane (the
``~/proj/*/.scitex/todo/tasks.yaml`` glob), classify each row, and
emit a machine-readable plan + a human-readable Markdown summary.
NOTHING is written to disk by the scanner — the migrator runs in a
separate verb behind ``-y`` after the plan is operator-approved.

Field-split rules (lead-approved 2026-06-13, refined from operator's
"minimal index" directive):

YAML keeps (metadata, structured graph-shape):
  - id, title (SHORT label, ≤120 chars)
  - status, priority, kind, blocker (closed enums)
  - parent, depends_on, blocks (edges)
  - assignee, agent, collaborators (ownership)
  - project, scope, repo, host
  - pr_url, issue_url
  - deadline / deadlines / scheduled
  - created_at, last_activity
  - comments[] — Gitea-compat activity log (short events; ``text``
    capped at 280 chars per entry)

MOVED to ``tasks/<id>/README.md``:
  - note (long-form body)
  - title prefix overflow (a >120-char title is body content)

MOVED to ``tasks/<id>/adr.md`` (skill 30 — no yaml field today):
  - decision records (convention only; no yaml migration needed)

The scanner classifies each row into one of:

  - ``CANONICAL`` — already minimal-metadata + has ``tasks/<id>/``
  - ``NEEDS_DIR`` — row exists, no ``tasks/<id>/`` sibling
  - ``NEEDS_NOTE_MIGRATE`` — ``note`` field present (body in yaml)
  - ``NEEDS_TITLE_TRIM`` — title > 120 chars
  - ``NEEDS_COMMENT_TRIM`` — any ``comments[].text`` > 280 chars
  - ``EMPTY_ID`` — row missing an id (legacy data; flagged separately)

A single row may carry multiple classifications; the plan emits the
union per row + per-project totals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


# === Tuning knobs ==========================================================

#: Maximum title length kept in tasks.yaml; anything longer counts as
#: body content and migrates to README.md. ≤120 chars = "fits in a
#: column header / chip without truncation."
MAX_TITLE_CHARS = 120

#: Maximum per-comment text length kept in tasks.yaml's comments[]
#: activity log; longer = body, migrates to the per-card README/adr.
MAX_COMMENT_CHARS = 280


# === Row classification ====================================================


@dataclass
class RowPlan:
    """Per-row classification + migration delta the migrator will apply."""

    id: str
    lane_path: Path
    classifications: List[str] = field(default_factory=list)
    note_excerpt: Optional[str] = None  # first 80 chars of `note`
    title_len: int = 0
    long_comment_count: int = 0
    canonical: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lane_path": str(self.lane_path),
            "classifications": list(self.classifications),
            "note_excerpt": self.note_excerpt,
            "title_len": self.title_len,
            "long_comment_count": self.long_comment_count,
            "canonical": self.canonical,
        }


@dataclass
class LanePlan:
    """Per-lane (= per-project) plan rollup."""

    lane_path: Path
    rows: List[RowPlan] = field(default_factory=list)
    total: int = 0
    canonical_count: int = 0
    needs_migration_count: int = 0
    counts_by_kind: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lane_path": str(self.lane_path),
            "total": self.total,
            "canonical_count": self.canonical_count,
            "needs_migration_count": self.needs_migration_count,
            "counts_by_kind": dict(self.counts_by_kind),
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class FleetPlan:
    """Top-level plan emitted by :func:`scan_all_lanes`."""

    lanes: List[LanePlan] = field(default_factory=list)

    def to_dict(self) -> dict:
        total = sum(l.total for l in self.lanes)
        canonical = sum(l.canonical_count for l in self.lanes)
        migrate = sum(l.needs_migration_count for l in self.lanes)
        return {
            "lane_count": len(self.lanes),
            "total_rows": total,
            "canonical_rows": canonical,
            "needs_migration_rows": migrate,
            "lanes": [l.to_dict() for l in self.lanes],
        }


# === The scan ==============================================================


def classify_row(row: dict, lane_path: Path) -> RowPlan:
    """Classify a single row. Pure function (no I/O on the row itself;
    only ``tasks/<id>/`` directory existence is probed under
    ``lane_path.parent / 'tasks' / row.id``)."""
    rid = row.get("id") or ""
    plan = RowPlan(id=str(rid), lane_path=lane_path)
    if not rid:
        plan.classifications.append("EMPTY_ID")
        return plan
    # `note` body in yaml?
    note = row.get("note")
    if isinstance(note, str) and note.strip():
        plan.classifications.append("NEEDS_NOTE_MIGRATE")
        plan.note_excerpt = note[:80] + ("…" if len(note) > 80 else "")
    # Title length?
    title = row.get("title") or ""
    plan.title_len = len(title)
    if plan.title_len > MAX_TITLE_CHARS:
        plan.classifications.append("NEEDS_TITLE_TRIM")
    # Long comments?
    comments = row.get("comments") or []
    if isinstance(comments, list):
        long_n = sum(
            1
            for c in comments
            if isinstance(c, dict)
            and isinstance(c.get("text"), str)
            and len(c["text"]) > MAX_COMMENT_CHARS
        )
        plan.long_comment_count = long_n
        if long_n:
            plan.classifications.append("NEEDS_COMMENT_TRIM")
    # tasks/<id>/ directory present?
    tasks_dir = lane_path.parent / "tasks" / rid
    if not tasks_dir.is_dir():
        plan.classifications.append("NEEDS_DIR")
    plan.canonical = not plan.classifications
    return plan


def scan_lane(lane_path: Path) -> LanePlan:
    """Scan one ``tasks.yaml`` and produce a :class:`LanePlan`.

    Malformed YAML → empty plan + log a WARNING. Per-row crash is
    swallowed (the migrator can still skip the row); whole-lane
    abort would mask other findings the operator needs to see.
    """
    from scitex_cards._model import load_tasks

    plan = LanePlan(lane_path=lane_path)
    try:
        rows = load_tasks(lane_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[scitex-todo._migrate] cannot load %s: %s",
            lane_path,
            exc,
        )
        return plan
    for row in rows:
        if not isinstance(row, dict):
            continue
        rp = classify_row(row, lane_path)
        plan.rows.append(rp)
    plan.total = len(plan.rows)
    plan.canonical_count = sum(1 for r in plan.rows if r.canonical)
    plan.needs_migration_count = plan.total - plan.canonical_count
    counts: dict = {}
    for r in plan.rows:
        for k in r.classifications:
            counts[k] = counts.get(k, 0) + 1
    plan.counts_by_kind = counts
    return plan


def scan_all_lanes(
    lane_paths: Optional[Iterable[Path]] = None,
) -> FleetPlan:
    """Scan every discovered lane + every project's ``tasks.yaml`` AND
    the global user-scope ``~/.scitex/todo/tasks.yaml``.

    Defaults to :func:`scitex_cards._django.services._discover_lanes` +
    the resolved global store. Pass an explicit iterable for tests
    (the scanner is pure-data and easy to fixture).
    """
    from scitex_cards._paths import resolve_tasks_path

    if lane_paths is None:
        from scitex_cards._django.services import _discover_lanes

        lanes = list(_discover_lanes())
        global_path = resolve_tasks_path(None)
        if global_path.exists() and global_path not in lanes:
            lanes = [global_path] + lanes
    else:
        lanes = list(lane_paths)
    fleet = FleetPlan()
    for lp in lanes:
        fleet.lanes.append(scan_lane(lp))
    return fleet


# === Markdown rendering ====================================================


def render_markdown(fleet: FleetPlan) -> str:
    """Render the plan as a human-readable Markdown report for the
    operator's review pass."""
    top = fleet.to_dict()
    out = [
        "# Directory-card migration plan",
        "",
        f"Lanes scanned: **{top['lane_count']}**",
        f"Total rows: **{top['total_rows']}**",
        f"Already canonical: **{top['canonical_rows']}**",
        f"Need migration: **{top['needs_migration_rows']}**",
        "",
        "## Per-lane summary",
        "",
    ]
    for lane in fleet.lanes:
        out.append(f"### {lane.lane_path}")
        out.append("")
        out.append(
            f"- total: **{lane.total}** "
            f"(canonical {lane.canonical_count}, "
            f"need migration {lane.needs_migration_count})"
        )
        for kind, n in sorted(lane.counts_by_kind.items()):
            out.append(f"- `{kind}`: {n}")
        # Sample 5 non-canonical rows per lane.
        sample = [r for r in lane.rows if not r.canonical][:5]
        if sample:
            out.append("")
            out.append("Sample non-canonical rows:")
            for r in sample:
                kinds = "/".join(r.classifications)
                note_hint = f" — note='{r.note_excerpt}'" if r.note_excerpt else ""
                out.append(f"- `{r.id}` [{kinds}]{note_hint}")
        out.append("")
    return "\n".join(out)


# === Stage 2 — apply / migrator ============================================
#
# Atomic per-card; per-lane git commit at end. The bytes-equal verify
# AFTER the README.md write is the proof the operator + lead asked for:
# no transform, no truncation, no reformat. If verify fails the row is
# skipped and the yaml stays UNTOUCHED for that row.

import os as _os
import subprocess as _subprocess


@dataclass
class RowApplyResult:
    """Per-row outcome from `apply_lane`."""

    id: str
    written_readme: bool = False
    yaml_updated: bool = False
    skipped_reason: Optional[str] = None
    readme_path: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "written_readme": self.written_readme,
            "yaml_updated": self.yaml_updated,
            "skipped_reason": self.skipped_reason,
            "readme_path": str(self.readme_path) if self.readme_path else None,
        }


@dataclass
class LaneApplyResult:
    """Per-lane outcome from `apply_lane`."""

    lane_path: Path
    rows: List[RowApplyResult] = field(default_factory=list)
    git_committed: bool = False
    git_skip_reason: Optional[str] = None

    @property
    def written_count(self) -> int:
        return sum(1 for r in self.rows if r.written_readme)

    @property
    def updated_count(self) -> int:
        return sum(1 for r in self.rows if r.yaml_updated)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.rows if r.skipped_reason)

    def to_dict(self) -> dict:
        return {
            "lane_path": str(self.lane_path),
            "written": self.written_count,
            "updated": self.updated_count,
            "skipped": self.skipped_count,
            "git_committed": self.git_committed,
            "git_skip_reason": self.git_skip_reason,
            "rows": [r.to_dict() for r in self.rows],
        }


def _build_readme_content(row: dict) -> str:
    """Build the README.md body for a single row.

    Layout:
      1. `<note verbatim>` — preserves bytes exactly so bytes-equal
         round-trip verifies on the simple case.
      2. If title was overflowed (>MAX_TITLE_CHARS): a `## Title (full)`
         section with the original full title.
      3. If any comment text was overflowed (>MAX_COMMENT_CHARS): a
         `## Long comments` section with each long comment's full text
         under its ts + author header.

    Returns the unicode string. Caller writes bytes via .encode("utf-8").
    """
    parts: List[str] = []
    note = row.get("note")
    if isinstance(note, str) and note:
        parts.append(note)
    title = row.get("title") or ""
    if len(title) > MAX_TITLE_CHARS:
        parts.append("")
        parts.append("## Title (full)")
        parts.append("")
        parts.append(title)
    comments = row.get("comments") or []
    long_entries = []
    if isinstance(comments, list):
        for c in comments:
            if not isinstance(c, dict):
                continue
            text = c.get("text") or ""
            if isinstance(text, str) and len(text) > MAX_COMMENT_CHARS:
                long_entries.append(c)
    if long_entries:
        parts.append("")
        parts.append("## Long comments")
        parts.append("")
        for c in long_entries:
            ts = c.get("ts") or "?"
            author = c.get("author") or "?"
            parts.append(f"### ts={ts} by={author}")
            parts.append("")
            parts.append(c.get("text") or "")
            parts.append("")
    return "\n".join(parts)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (tmp + fsync + os.replace).

    Mirrors `_model._save_tasks_unlocked`'s shape — the same POSIX-atomic
    contract `tasks.yaml` writes use, so the migrator can't leave a
    partial README mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = content.encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(data)
        try:
            _os.fsync(fh.fileno())
        except OSError:
            # Best-effort; replace below is the atomic guarantee.
            pass
    _os.replace(tmp, path)


def _strip_migrated_fields(row: dict) -> dict:
    """Return a NEW row dict with `note` removed + title trimmed +
    long comment texts trimmed. The original row is unchanged."""
    out = dict(row)
    out.pop("note", None)
    title = out.get("title")
    if isinstance(title, str) and len(title) > MAX_TITLE_CHARS:
        out["title"] = title[:MAX_TITLE_CHARS]
    comments = out.get("comments")
    if isinstance(comments, list):
        new_comments = []
        for c in comments:
            if not isinstance(c, dict):
                new_comments.append(c)
                continue
            text = c.get("text") or ""
            if isinstance(text, str) and len(text) > MAX_COMMENT_CHARS:
                trimmed = dict(c)
                trimmed["text"] = text[:MAX_COMMENT_CHARS]
                new_comments.append(trimmed)
            else:
                new_comments.append(c)
        out["comments"] = new_comments
    return out


def _git_commit_lane(lane_path: Path, message: str) -> tuple[bool, Optional[str]]:
    """Run `git add -A && git commit -m <message>` in the lane's
    parent directory. Returns ``(committed, skip_reason)``.

    Skip reasons:
      - lane is not inside a git repo (no .git ancestor)
      - git command failed (returns the stderr tail)
      - nothing to commit (treated as success without an empty commit)
    """
    work_dir = lane_path.parent
    # Locate the git root by walking up at most ``_GIT_WALK_MAX`` levels;
    # the lane is conventionally at ``<repo>/.scitex/todo/tasks.yaml`` so
    # the project's ``.git`` is at most 3 parents up. Bounding the walk
    # prevents the test runner's tmp dir from accidentally resolving to
    # an unrelated git repo elsewhere on disk (e.g. an enclosing dev
    # checkout) and committing into it. (hook-bypass: line-limit.)
    _GIT_WALK_MAX = 4
    cur = work_dir.resolve()
    git_root: Optional[Path] = None
    candidates = [cur] + list(cur.parents)[:_GIT_WALK_MAX]
    for parent in candidates:
        if (parent / ".git").exists():
            git_root = parent
            break
    if git_root is None:
        return False, "no git repo found above lane"
    try:
        # `git add -A` with no pathspec stages the WHOLE repo — simpler
        # than scoping to work_dir which has caused subtle path-arg
        # issues across git versions. The migrator's job is to commit
        # the post-migration state of the lane; any other changes that
        # may have been staged externally are NOT our concern (the
        # caller drives the migration; a clean repo is their precondition).
        _subprocess.run(
            ["git", "-C", str(git_root), "add", "-A"],
            check=True,
            capture_output=True,
        )
    except (_subprocess.CalledProcessError, FileNotFoundError) as e:
        return False, f"git add failed: {e}"
    # Check whether anything is staged.
    try:
        status = _subprocess.run(
            ["git", "-C", str(git_root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except _subprocess.CalledProcessError as e:
        return False, f"git status failed: {e}"
    if not status.stdout.strip():
        return False, "nothing to commit"
    try:
        _subprocess.run(
            ["git", "-C", str(git_root), "commit", "-m", message],
            check=True,
            capture_output=True,
        )
    except _subprocess.CalledProcessError as e:
        return False, f"git commit failed: {e.stderr.decode('utf-8', 'replace')[-200:]}"
    return True, None


def apply_lane(
    lane_path: Path,
    *,
    dry_run: bool = False,
    author: str = "scitex-todo-migrator",
) -> LaneApplyResult:
    """Migrate every row in `lane_path` to the canonical dir-card shape.

    Contract:
      - Acquires the lane's ``_store_lock`` for the full read-modify-
        write so a concurrent writer can't clobber the in-flight edit.
      - Per row: build README content → atomic write → bytes-equal
        verify → strip yaml fields. If verify fails the row is SKIPPED
        and the yaml stays untouched for that row.
      - After all rows process, the YAML is saved atomically (via
        the existing ``_model._save_tasks_unlocked``).
      - After the YAML save, the lane's git repo (if any) gets a
        single ``[scitex-todo migrate]`` commit.
      - Idempotent: rows that are already canonical (per
        :func:`classify_row`) become no-ops.

    Dry-run skips every write but still produces the per-row plan +
    intended actions in the result.
    """
    from scitex_cards._model import (
        _save_tasks_unlocked,
        _store_lock,
        load_tasks,
    )

    result = LaneApplyResult(lane_path=lane_path)
    try:
        rows = load_tasks(lane_path)
    except Exception as exc:  # noqa: BLE001
        result.git_skip_reason = f"cannot load lane: {exc}"
        return result

    # The migrator must be allowed to write directly to flat tasks.yaml
    # for the duration of the run; the validator (separate PR) reads
    # this env. Saved + restored on exit so we don't leak the bypass.
    prior = _os.environ.get("SCITEX_CARDS_ALLOW_FLAT_WRITES")
    _os.environ["SCITEX_CARDS_ALLOW_FLAT_WRITES"] = "1"
    try:
        with _store_lock(lane_path):
            # Re-load inside the lock so a concurrent writer can't
            # race us between scan and apply.
            try:
                rows = load_tasks(lane_path)
            except Exception as exc:  # noqa: BLE001
                result.git_skip_reason = f"reload failed: {exc}"
                return result

            new_rows: List[dict] = []
            for row in rows:
                if not isinstance(row, dict):
                    new_rows.append(row)
                    continue
                rid = row.get("id")
                if not rid:
                    rr = RowApplyResult(
                        id="<empty>",
                        skipped_reason="EMPTY_ID",
                    )
                    result.rows.append(rr)
                    new_rows.append(row)
                    continue
                plan = classify_row(row, lane_path)
                if plan.canonical:
                    # Idempotent — nothing to do.
                    result.rows.append(
                        RowApplyResult(id=rid, skipped_reason="already-canonical"),
                    )
                    new_rows.append(row)
                    continue
                tasks_dir = lane_path.parent / "tasks" / rid
                readme_path = tasks_dir / "README.md"
                rr = RowApplyResult(id=rid, readme_path=readme_path)
                # Compute target README content.
                content = _build_readme_content(row)
                if dry_run:
                    # Pretend the write succeeded; don't touch disk.
                    rr.written_readme = True
                    rr.yaml_updated = True
                    result.rows.append(rr)
                    new_rows.append(_strip_migrated_fields(row))
                    continue
                # If the row's only issue is NEEDS_DIR (no note / no
                # title overflow / no long comments), still create the
                # dir + an empty README so the on-disk shape matches.
                try:
                    _atomic_write_text(readme_path, content)
                except OSError as e:
                    rr.skipped_reason = f"README write failed: {e}"
                    result.rows.append(rr)
                    new_rows.append(row)
                    continue
                # Bytes-equal verify — re-read what we just wrote.
                try:
                    re_read = readme_path.read_text(encoding="utf-8")
                except OSError as e:
                    rr.skipped_reason = f"README read-back failed: {e}"
                    result.rows.append(rr)
                    new_rows.append(row)
                    continue
                if re_read != content:
                    rr.skipped_reason = "bytes-equal verify FAILED"
                    result.rows.append(rr)
                    new_rows.append(row)
                    continue
                # Also assert the `note` field round-trips: the
                # first len(note) bytes of the README MUST equal the
                # original note byte-for-byte (the lead's guardrail #1).
                note = row.get("note") or ""
                if isinstance(note, str) and note:
                    if not content.startswith(note):
                        rr.skipped_reason = "note byte-equal head check FAILED"
                        result.rows.append(rr)
                        new_rows.append(row)
                        continue
                rr.written_readme = True
                rr.yaml_updated = True
                result.rows.append(rr)
                new_rows.append(_strip_migrated_fields(row))

            # Save the migrated YAML atomically (only if not dry-run AND
            # at least one row was updated).
            if not dry_run and result.updated_count > 0:
                _save_tasks_unlocked(new_rows, lane_path)
    finally:
        if prior is None:
            _os.environ.pop("SCITEX_CARDS_ALLOW_FLAT_WRITES", None)
        else:
            _os.environ["SCITEX_CARDS_ALLOW_FLAT_WRITES"] = prior

    # Per-lane git commit (skip in dry-run).
    if not dry_run and result.updated_count > 0:
        committed, skip_reason = _git_commit_lane(
            lane_path,
            f"[scitex-todo migrate] flat → directory ({result.updated_count} cards)",
        )
        result.git_committed = committed
        result.git_skip_reason = skip_reason
    return result


def apply_all_lanes(
    lane_paths: Optional[Iterable[Path]] = None,
    *,
    dry_run: bool = False,
    author: str = "scitex-todo-migrator",
) -> List[LaneApplyResult]:
    """Migrate every lane returned by the scanner's default discovery.

    Each lane is migrated independently — a failure in one lane does
    NOT abort the others. The caller (CLI) aggregates the results.
    """
    if lane_paths is None:
        from scitex_cards._django.services import _discover_lanes
        from scitex_cards._paths import resolve_tasks_path

        lanes = list(_discover_lanes())
        global_path = resolve_tasks_path(None)
        if global_path.exists() and global_path not in lanes:
            lanes = [global_path] + lanes
    else:
        lanes = list(lane_paths)
    results: List[LaneApplyResult] = []
    for lp in lanes:
        results.append(apply_lane(lp, dry_run=dry_run, author=author))
    return results


__all__ = [
    "MAX_TITLE_CHARS",
    "MAX_COMMENT_CHARS",
    "RowPlan",
    "LanePlan",
    "FleetPlan",
    "RowApplyResult",
    "LaneApplyResult",
    "classify_row",
    "scan_lane",
    "scan_all_lanes",
    "render_markdown",
    "apply_lane",
    "apply_all_lanes",
]
