#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project-group definitions for the scitex-todo board.

A *group* is a user-defined cluster of projects that the board can render
as one collapsible header above the per-project columns. Two operator
shapes drove the design (lead a2a 2026-06-12):

* **Active-collaboration cluster** — e.g.
  ``{scitex-agent-container, paper-scitex-clew}`` while those two are
  exchanging high-bandwidth a2a; the operator wants their columns
  visually adjacent + collapsible-as-one.
* **All-spanning member** — e.g. ``{lead}`` belongs to every project
  group simultaneously; it is rendered as a horizontal strip ABOVE the
  per-project grid rather than inside any one group.

Groups are a *viewer* concern; they do not change task semantics, are
not exported to Gitea, and may overlap freely (the same project may
appear in multiple groups).

Storage
-------
The group list lives at the legacy store's top level next to ``tasks:``:

.. code-block:: text

    groups:
      - id: collab-clew-ac
        label: "Active collab: clew × agent-container"
        projects: [paper-scitex-clew, scitex-agent-container]
        color: "#f4a460"          # optional, hex / CSS color
      - id: lead
        label: "lead (spans every project)"
        spans_all: true            # NO `projects` when spans_all
        color: "#ffd700"

    tasks: [...]

Pure additive: stores without ``groups:`` return ``[]`` and the board
falls back to today's flat column view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._model import TaskValidationError
from ._yaml import safe_load


@dataclass(frozen=True)
class Group:
    """A user-defined cluster of projects.

    Attributes
    ----------
    id : str
        Stable identifier. Unique across ``groups`` AND must not collide
        with any task ``id`` (keeps the namespace clean for the FE wire).
    label : str
        Display name for the group header.
    projects : tuple[str, ...]
        Project names this group contains. Empty when ``spans_all`` is
        True. The same project may appear in multiple groups (overlap is
        deliberate; e.g. a project may belong to both an "active collab"
        cluster and a "research" cluster).
    spans_all : bool
        Render this group across every project (operator's "lead" case).
        Mutually exclusive with a non-empty ``projects`` list.
    color : str | None
        Optional CSS color for the group header swatch. None → FE picks
        a default.
    """

    id: str
    label: str
    projects: tuple[str, ...] = ()
    spans_all: bool = False
    color: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the wire shape used by the ``/graph`` JSON endpoint."""
        out: dict[str, Any] = {"id": self.id, "label": self.label}
        if self.spans_all:
            out["spans_all"] = True
        else:
            out["projects"] = list(self.projects)
        if self.color is not None:
            out["color"] = self.color
        return out


def load_groups(path: str | Path, *, task_ids: set[str] | None = None) -> list[Group]:
    """Load and validate the ``groups:`` list from a YAML store.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store.
    task_ids : set[str], optional
        If supplied, group ids are checked for collision against this
        set (a group id MUST NOT match any task id). Pass the result of
        ``[t["id"] for t in load_tasks(path)]`` to enforce this.

    Returns
    -------
    list[Group]
        The validated groups, in document order. Empty list if the YAML
        has no ``groups:`` key — a store without groups behaves as if
        no groups are defined and the board falls back to its flat
        column view.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        On any structural fault (missing id/label, non-string
        projects, both ``spans_all`` and ``projects``, duplicate id,
        or collision with a task id when ``task_ids`` is provided).
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"task store not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}

    raw = data.get("groups")
    if raw is None:
        return []
    return _validate_groups(raw, source=str(path), task_ids=task_ids)


def _validate_groups(
    raw: object,
    *,
    source: str,
    task_ids: set[str] | None = None,
) -> list[Group]:
    """Validate a raw ``groups:`` value and return a list of :class:`Group`.

    The single validation gate for the group schema, shared by
    :func:`load_groups` and any future writer. Validates:

    * top-level shape (list of mappings);
    * required fields (``id``, ``label``) — both non-empty strings;
    * ``projects`` is a list of non-empty strings when present;
    * ``spans_all`` is a bool when present;
    * ``spans_all=True`` AND non-empty ``projects`` is rejected;
    * ``color`` is a string when present;
    * ids are unique across the groups list;
    * ids do not collide with any provided task id.

    Parameters
    ----------
    raw : object
        The candidate ``groups`` value (must be a list of mappings).
    source : str
        Label for error messages (the store path, or ``"<save_groups>"``).
    task_ids : set[str], optional
        Task-id collision set; see :func:`load_groups`.
    """
    if not isinstance(raw, list):
        raise TaskValidationError(
            f"{source}: top-level 'groups' must be a list (got {type(raw).__name__})"
        )

    seen_ids: set[str] = set()
    out: list[Group] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TaskValidationError(
                f"{source}: groups[{idx}] must be a mapping (got {type(entry).__name__})"
            )

        # `id`
        gid = entry.get("id")
        if not isinstance(gid, str) or not gid.strip():
            raise TaskValidationError(
                f"{source}: groups[{idx}].id must be a non-empty string"
            )
        if gid in seen_ids:
            raise TaskValidationError(f"{source}: duplicate group id {gid!r}")
        if task_ids and gid in task_ids:
            raise TaskValidationError(
                f"{source}: group id {gid!r} collides with a task id "
                f"(group ids and task ids share one namespace)"
            )
        seen_ids.add(gid)

        # `label`
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise TaskValidationError(
                f"{source}: groups[{idx}] ({gid!r}).label must be a non-empty string"
            )

        # `spans_all`
        spans_all_raw = entry.get("spans_all", False)
        if not isinstance(spans_all_raw, bool):
            raise TaskValidationError(
                f"{source}: groups[{idx}] ({gid!r}).spans_all must be a boolean"
            )

        # `projects`
        projects_raw = entry.get("projects")
        projects: tuple[str, ...] = ()
        if projects_raw is not None:
            if not isinstance(projects_raw, list):
                raise TaskValidationError(
                    f"{source}: groups[{idx}] ({gid!r}).projects must be a list"
                )
            for j, p in enumerate(projects_raw):
                if not isinstance(p, str) or not p.strip():
                    raise TaskValidationError(
                        f"{source}: groups[{idx}] ({gid!r}).projects[{j}] "
                        f"must be a non-empty string"
                    )
            projects = tuple(projects_raw)

        if spans_all_raw and projects:
            raise TaskValidationError(
                f"{source}: groups[{idx}] ({gid!r}) cannot set both "
                f"spans_all=true AND a non-empty projects list"
            )
        if not spans_all_raw and not projects:
            raise TaskValidationError(
                f"{source}: groups[{idx}] ({gid!r}) needs either spans_all=true "
                f"or a non-empty projects list"
            )

        # `color`
        color_raw = entry.get("color")
        if color_raw is not None and not isinstance(color_raw, str):
            raise TaskValidationError(
                f"{source}: groups[{idx}] ({gid!r}).color must be a string"
            )

        out.append(
            Group(
                id=gid,
                label=label,
                projects=projects,
                spans_all=spans_all_raw,
                color=color_raw,
            )
        )

    return out


# Make the dataclass picklable + idempotent under `field()` re-import.
_ = field  # noqa: F841 — silence unused-import for forward compat
