#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the reconcile scan set (pending abolition).

``_reconcile_prs`` module prose, the ``reconcile-merged-prs`` CLI help, and
the ``_jobs_provider`` JobSpec comment still named ``pending`` / ``blocked``
as scanned states after ``pending`` was abolished 2026-07-10 and ``blocked``
left the set 2026-08-03. The code was already correct (``OPEN_STATUSES ==
{"in_progress"}``) — only the prose lied, inviting the next copy-paste to
reintroduce a dead scan filter. The prose now names ``in_progress``; these
pins keep code, docs, and the decision core aligned.

These pins use the pure decision core + docstrings only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import scitex_cards._reconcile_prs as reconcile_mod
from scitex_cards._cli import _reconcile as reconcile_cli_mod
from scitex_cards._reconcile_prs import (
    ACTION_CLOSE,
    ACTION_SKIP_NOT_OPEN,
    ACTION_SKIP_UNKNOWN,
    MERGED,
    OPEN_STATUSES,
    UNKNOWN,
    decide_reconcile_action,
)

_PR = "https://github.com/o/r/pull/1"


def test_open_statuses_is_in_progress_only():
    assert OPEN_STATUSES == frozenset({"in_progress"})


def test_merged_pr_closes_only_in_progress():
    assert (
        decide_reconcile_action({"status": "in_progress", "pr_url": _PR}, MERGED)
        == ACTION_CLOSE
    )


def test_merged_pr_skips_excluded_and_abolished_statuses():
    for status in ("blocked", "deferred", "pending", "cancelled", "failed", "goal"):
        assert (
            decide_reconcile_action({"status": status, "pr_url": _PR}, MERGED)
            == ACTION_SKIP_NOT_OPEN
        )


def test_module_docstring_names_in_progress_scan_set():
    doc = reconcile_mod.__doc__ or ""
    assert "``in_progress``" in doc
    assert "(and some ``pending``)" not in doc
    assert "``in_progress`` / ``blocked``" not in doc


def test_cli_and_jobspec_prose_name_in_progress_scan_set():
    cli_doc = reconcile_cli_mod.__doc__ or ""
    assert "pending / in_progress / blocked" not in cli_doc
    assert "``in_progress`` cards" in cli_doc
    help_text = getattr(reconcile_cli_mod.reconcile_merged_prs_cmd, "help", "") or ""
    assert "pending / in_progress / blocked" not in help_text
    assert "in_progress cards" in help_text


def test_unknown_fixture_seeds_a_live_open_status():
    """The shared ``_store`` fixture must not seed the abolished status.

    ``test_unknown_merge_state_never_closes`` pins the UNKNOWN fail-soft
    path, which only an OPEN card can exercise (OPEN_STATUSES is
    in_progress-only). A ``status: pending`` seed passes that test via
    SKIP_NOT_OPEN for the wrong reason, masking the path it claims to pin.
    """
    from pathlib import Path

    source = Path(__file__).with_name("test__reconcile_prs.py").read_text()
    assert "unknown-pr-card" in source  # non-vacuity: the fixture is here
    assert "status: pending" not in source
    assert "status: in_progress" in source


def test_pending_seed_would_mask_the_fail_soft_path():
    """Guard the masking mechanism itself: pending + UNKNOWN short-circuits.

    The decision core checks openness BEFORE merge-state, so a legacy
    ``pending`` row with an unknowable PR returns SKIP_NOT_OPEN, never
    SKIP_UNKNOWN. This is why the fixture must stay ``in_progress`` — the
    two are not interchangeable seeds.
    """
    assert (
        decide_reconcile_action({"status": "pending", "pr_url": _PR}, UNKNOWN)
        == ACTION_SKIP_NOT_OPEN
    )
    assert (
        decide_reconcile_action({"status": "in_progress", "pr_url": _PR}, UNKNOWN)
        == ACTION_SKIP_UNKNOWN
    )


def test_jobspec_prose_names_in_progress_scan_set():
    """The ``_jobs_provider`` JobSpec comment is the third prose surface.

    The module docstring up top promises this surface is pinned ("the
    ``reconcile-merged-prs`` CLI help, and the ``_jobs_provider`` JobSpec
    comment"), but the CLI-only test above never reads it — so a future
    edit could reintroduce the dead ``pending / in_progress / blocked``
    scan set in the cron spec while every pin stays green. ``inspect``
    keeps this PG-free (no JobSpec instantiation, no store, no
    PostgreSQL); the non-vacuity anchor is the ``reconcile-merged-prs``
    marker the block has carried since before the repoint.
    """
    import inspect

    import scitex_cards._jobs_provider as jobs_provider_mod

    src = inspect.getsource(jobs_provider_mod.provide_jobs)
    assert "reconcile-merged-prs" in src  # non-vacuity: the cron block is here
    assert "pending / in_progress /" not in src
    assert "OPEN_STATUSES" in src
    assert "in_progress" in src
