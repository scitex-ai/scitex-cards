#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every JobSpec this package declares must name THIS package, not another.

The package was renamed to scitex-cards. The operator's instruction was that
the rename be complete rather than incremental (2026-08-15, verbatim: 「new
only というのはダメです」 / 「全部ハードにしないと」 — roughly "new-only is not
acceptable" / "it has to be done hard, all of it"), on the grounds that a
half-finished rename accrues interest: 「負債は利子が広がって大きくなりますよ」.

WHY THIS IS A TEST AND NOT A ONE-TIME EDIT. The strings here become things
the fleet runs: ``command`` is the argv a host executes unattended, ``name``
is the identity a supervisor schedules under. A stale package name in either
is not a cosmetic typo — it is either a command that invokes a console script
that is no longer installed, or a unit registered under a name nobody greps
for anymore. Both fail quietly, on a timer, on a machine nobody is watching.

An edit fixes today's file. Only a check fixes tomorrow's, and the next
JobSpec will be added by someone who was not part of the rename.

THIS FILE USED TO BE A BLOCKLIST, AND THE BLOCKLIST DISARMED ITSELF. It held
the retired name in a ``RETIRED_NAME`` constant and asserted that no field
contained it. When the repo-wide sweep replaced every occurrence of the
retired name, it replaced this one too — so the guard began asserting that no
JobSpec contains the CURRENT name, which every JobSpec does, and the file went
red for the exact opposite of its purpose. A test that names a forbidden
string is a test that a later rename can turn inside out.

SO IT IS NOW AN ALLOWLIST, which is both rename-proof and strictly stronger.
Instead of "does this field contain one specific bad name", it asks "does
every ``scitex-``-prefixed token in this field name THIS package". That still
rejects the retired name, and additionally rejects a JobSpec that wanders off
to some third scitex package by typo — which the blocklist never caught.
"""

from __future__ import annotations

import re

import pytest

from scitex_cards._jobs_provider import provide_jobs

#: This package, as it appears in a shell or a unit name.
OUR_NAME = "scitex-cards"

#: Any ecosystem-package-looking token: ``scitex-`` plus a lowercase word,
#: with further ``-word`` segments. ``scitex-cards-wake-watcher`` matches as
#: one token, which is what we want — it is checked against OUR_NAME by
#: prefix, so a job named after us with a suffix is fine.
_SCITEX_TOKEN = re.compile(r"scitex-[a-z0-9]+(?:-[a-z0-9]+)*")

#: The JobSpec fields whose contents are executed or scheduled, as opposed to
#: merely read by a human.
LOAD_BEARING_FIELDS = ("name", "command")


def _foreign_tokens(text: str) -> list[str]:
    """``scitex-*`` tokens in ``text`` that do not name this package.

    A token names this package when it IS ``scitex-cards`` or extends it with
    a ``-suffix`` (``scitex-cards-notify``). Anything else — the retired name,
    a sibling package, a typo — is foreign and comes back for the caller to
    fail on.
    """
    return [
        tok
        for tok in _SCITEX_TOKEN.findall(text or "")
        if tok != OUR_NAME and not tok.startswith(OUR_NAME + "-")
    ]


def _scheduled_jobs():
    """Every job this package asks the host to run unattended."""
    return list(provide_jobs())


class TestTheCheckItselfCanFail:
    """Guard the guard — twice, because both halves can rot independently."""

    def test_at_least_one_job_is_provided(self):
        # Arrange
        provider = provide_jobs
        # Act
        jobs = list(provider())
        # Assert — an empty provider would satisfy every "no offenders"
        # assertion below while checking nothing.
        assert jobs, (
            "provide_jobs() returned nothing, so the checks in this file would "
            "pass vacuously. That is the 'gate that cannot fail' shape."
        )

    def test_a_foreign_package_name_is_actually_detected(self):
        # Arrange — the positive control the old blocklist never had. If the
        # detector stops detecting, every assertion below passes for free.
        wrong = "scitex-somethingelse run --daemon"
        # Act
        foreign = _foreign_tokens(wrong)
        # Assert
        assert foreign == ["scitex-somethingelse"]

    def test_our_own_name_with_a_suffix_is_not_flagged(self):
        # Arrange — the other way the detector can rot: flagging everything.
        ours = "scitex-cards deliver --once"
        # Act
        foreign = _foreign_tokens(ours)
        # Assert
        assert foreign == []


class TestJobSpecsNameThisPackage:
    """The rename reached the strings the fleet actually runs."""

    @pytest.mark.parametrize("field", LOAD_BEARING_FIELDS)
    def test_no_load_bearing_field_names_another_package(self, field):
        # Arrange
        jobs = _scheduled_jobs()
        # Act
        offenders = {
            j.name: _foreign_tokens(getattr(j, field) or "")
            for j in jobs
            if _foreign_tokens(getattr(j, field) or "")
        }
        # Assert
        assert not offenders, (
            f"job(s) {offenders} name a package that is not {OUR_NAME!r} in "
            f"their {field!r}. A stale name here is executed, not just read: "
            f"as a `command` it invokes a console script we do not ship, and "
            f"as a `name` it schedules under an identity the fleet no longer "
            f"greps for."
        )

    def test_no_description_names_another_package(self):
        # Arrange
        jobs = _scheduled_jobs()
        # Act
        offenders = {
            j.name: _foreign_tokens(j.description or "")
            for j in jobs
            if _foreign_tokens(j.description or "")
        }
        # Assert — descriptions are what the operator reads in `systemctl
        # status` to decide whether a unit is the one they meant.
        assert not offenders, (
            f"job(s) {offenders} describe themselves using a package name "
            f"that is not {OUR_NAME!r}, which is the text shown next to the "
            f"unit in systemctl output"
        )


# EOF
