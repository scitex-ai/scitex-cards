#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No SCHEDULED job may rebuild the database from a YAML file.

WHY THIS IS A TEST AND NOT A COMMENT. `db snapshot --refresh` imports a YAML
document into ``cards.db``, replacing every row with whatever that document
contains. While YAML was canonical this was the correct first half of the
backup rail — the import WAS the freshness step. Once the database is the
store, the same flag becomes a data-loss engine ON A TIMER: every hour it
overwrites the DB, including every card written in the preceding hour, with
the contents of a frozen file. It logs a successful refresh and a successful
push while doing it.

On 2026-07-20 the live board went from 2,165 cards to 5 when an import pulled
a 1,349-byte fixture over it. `--refresh` is the flag that performs that
import. A scheduled caller is the worst possible caller for it, because
nobody is watching at the moment it runs.

WHAT MADE THIS WORTH PINNING RATHER THAN JUST FIXING. The generated systemd
unit on the host carried `--refresh --push`, and a hand-written drop-in
(`no-yaml-refresh.conf`) reset ExecStart to the safe form. Measured
2026-07-20, the resolved ExecStart was `db snapshot --push` — so the rail was
genuinely not firing destructively. It was safe because a SECOND FILE
cancelled the first one.

That is not a safe system. Delete the drop-in, regenerate the unit from this
provider, or write a new unit under a different name, and the destructive form
returns silently. A dangerous declaration plus a corrective override is strictly
worse than a safe declaration, because the override is invisible at the place
anyone reads to learn what the job does.

So the barrier belongs on the DECLARATION, where the unit is generated from.
"""

from __future__ import annotations

import pytest

from scitex_cards._jobs_provider import provide_jobs

#: Flags that make a command read a YAML document and write database rows.
#: Extend this if a new import spelling appears — the point is the CAPABILITY,
#: not this particular string.
YAML_IMPORT_FLAGS = ("--refresh", "--from-yaml")


def _scheduled_jobs():
    """Every job this package asks the host to run unattended."""
    return list(provide_jobs())


class TestNoScheduledJobImportsYaml:
    """A timer must never be able to rebuild the store from a document."""

    def test_at_least_one_job_is_provided(self):
        # Arrange / Act
        jobs = _scheduled_jobs()
        # Assert — guard the guard: an empty provider would make every
        # assertion below pass while testing nothing at all.
        assert jobs, (
            "provide_jobs() returned nothing, so the checks in this file would "
            "pass vacuously. That is the 'gate that cannot fail' shape."
        )

    @pytest.mark.parametrize("flag", YAML_IMPORT_FLAGS)
    def test_no_scheduled_command_carries_a_yaml_import_flag(self, flag):
        # Arrange
        jobs = _scheduled_jobs()
        # Act
        offenders = [j.name for j in jobs if flag in (j.command or "")]
        # Assert
        assert not offenders, (
            f"scheduled job(s) {offenders} declare `{flag}`, which rebuilds "
            f"the database from a YAML document on a timer. This is how the "
            f"board went from 2,165 cards to 5 on 2026-07-20. If a rebuild is "
            f"genuinely needed, run it by hand where a human sees the result."
        )

    def test_the_snapshot_job_still_pushes_off_site(self):
        # Arrange
        jobs = {j.name: j for j in _scheduled_jobs()}
        # Act
        snapshot = jobs.get("scitex-cards.snapshot")
        # Assert — removing `--refresh` must not quietly remove the BACKUP too.
        # The export is the operator's stated fallback and deleting it would
        # take the safety net away at the moment it matters most.
        assert snapshot is not None, "the snapshot job disappeared entirely"
        assert "--push" in snapshot.command, (
            "the snapshot job no longer pushes off-site; the hourly rail would "
            "commit locally and read as 'backed up' with no remote copy"
        )


# EOF
