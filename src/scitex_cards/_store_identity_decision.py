#!/usr/bin/env python3
"""ONE decision body for "am I on the store I think I am".

WHY THIS MODULE EXISTS, and it is not tidiness. Until 2026-08-19 there were TWO
comparison bodies with the same four outcomes:

    _store_instance.check_store_identity   takes a live connection   (health)
    _store_pin._check_against              takes a probed value      (resolve_store)

``_check_against``'s own docstring said the outcomes were "kept IDENTICAL to
``check_store_identity``'s — two guards that disagree about what 'cannot tell'
means is the collapse this whole family prevents". KEPT IDENTICAL BY DISCIPLINE,
which is precisely the arrangement that drifts. They differ only in HOW they
obtain the values, so that is the only difference that should exist: both now
probe, then delegate here.

THE DEFECT THIS CLOSES, found by dotfiles 2026-08-17 by MUTATION-TESTING the gate
rather than reading it — they set a deliberately wrong value and checked whether
the gate noticed::

    SCITEX_CARDS_STORE_UUID=deadbeef-1111-4222-8333-444455556666 \\
    SCITEX_CARDS_STORE_INSTANCE=7672112238472680366 \\
      scitex-cards resolve-store --json

    "store_uuid":       "1d55dd6e-3d2a-4c24-a429-a78835ab988f"
    "expected_uuid":    "deadbeef-1111-4222-8333-444455556666"
    "identity_verdict": "matches"        <-- observed != expected, adjacent lines
    "may_proceed":      true

The uuid was read from the environment and surfaced into the payload, and never
reached a comparison: both call sites passed the INSTANCE into a single
``expected`` field. THE COLLAPSE OF TWO INDEPENDENT EXPECTATIONS INTO ONE FIELD
IS THE BUG, so this module keeps them as separate parameters — that separation is
what stops a future edit re-creating it.

WHY BOTH HALVES ARE REQUIRED, AND NEITHER SUFFICES. They catch different
failures, and each has a live reproduction on this fleet:

  * ``store_uuid`` is a ``schema_meta`` ROW, so a dump/restore carries it. Three
    databases once answered ``1d55dd6e-3d2a-4c24-a429-a78835ab988f`` while
    holding different data — measured 2026-08-12, ~300 cards apart. So the uuid
    alone cannot detect a fork; that is what makes a fork a fork.
  * ``system_identifier`` is minted by initdb and travels in no dump, but it
    identifies the SERVER. A database frozen and restored on the SAME physical
    server keeps it and gets a NEW ``store_uuid``. That is the 2026-08-09
    incident this pin exists to prevent — a store frozen eight days earlier
    serving the fleet while looking healthy.

Measured 2026-08-17, two ports on one host, which is both cases at once::

    nas-03 55432 (local)      system_identifier 7674038125294264831
    nas-03 55433 (tunnelled)  system_identifier 7672112238472680366   DIFFER
    both                      store_uuid        1d55dd6e-…            SAME

ALONGSIDE, NEVER INSTEAD OF. Because only the instance half was compared, that
case is currently caught by accident; a repair that "simplified" toward the uuid
— the value whose own name says it is *the* store identity — would convert a
working guard into one that passes a week-old board. The uuid is compared FIRST
here so an uuid mismatch can never again be masked by the instance agreeing, but
first is not instead.

A HALF-PIN IS ``CANNOT_TELL``, NOT ``MATCHES``, and that is a deliberate change
from the shape dotfiles first proposed. Satisfying only the instance answers
which SERVER and never which DATABASE; satisfying only the uuid answers which
BOARD and never which SERVER. Neither is the question the caller asked, so
neither earns a pass. The cost — a deliberate instance-only pin now refuses —
has blast radius zero today because ``may_proceed`` still has no production
consumer, and that sequencing is the right way round: fix the verdict while
nothing acts on it, then wire a consumer against semantics already correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - annotations only, avoids an import cycle
    from ._store_instance import StoreInstance


class IdentityVerdict(str, Enum):
    """Whether the connected store is the one the caller expected.

    THREE-VALUED, and the third value is the reason this enum exists rather
    than a bool. An expectation that is not pinned reads ``None``, so the
    comparison it feeds has no right-hand side and cannot fail — a gate that
    cannot fail is not a gate. ``CANNOT_TELL`` gives that state a name so a
    caller must handle it rather than inherit it as a pass.
    """

    MATCHES = "matches"
    DIFFERS = "differs"
    CANNOT_TELL = "cannot-tell"


@dataclass(frozen=True)
class IdentityCheck:
    """The answer to "am I connected to the store I think I am".

    Attributes
    ----------
    verdict : IdentityVerdict
    observed : StoreInstance
        What the connection actually reached.
    expected : str or None
        The pinned INSTANCE (``system_identifier``), verbatim, so an error can
        print both sides rather than asserting a mismatch the reader cannot
        check.
    reason : str or None
        Why the answer is not ``MATCHES``. ``None`` only on ``MATCHES``.
    observed_uuid : str or None
        The store's own ``schema_meta.store_uuid``, or ``None`` when it could
        not be read.
    expected_uuid : str or None
        The pinned UUID. SEPARATE from ``expected`` on purpose: collapsing the
        two expectations into one field is the 2026-08-17 defect.
    """

    verdict: IdentityVerdict
    observed: "StoreInstance"
    expected: Optional[str] = None
    reason: Optional[str] = None
    observed_uuid: Optional[str] = None
    expected_uuid: Optional[str] = None

    def __post_init__(self) -> None:
        """A non-matching verdict must say why; a matching one must not."""
        if self.verdict is IdentityVerdict.MATCHES:
            if self.reason is not None:
                raise ValueError(
                    f"IdentityCheck(MATCHES) carries a reason — a reason "
                    f"explains a refusal: {self.reason!r}"
                )
            return
        if not self.reason:
            raise ValueError(
                f"IdentityCheck({self.verdict.value}) with no reason — a "
                "refusal a caller cannot print is a refusal nobody can act on"
            )

    @property
    def may_proceed(self) -> bool:
        """Only ``MATCHES`` proceeds. ``CANNOT_TELL`` refuses like ``DIFFERS``.

        Named as a question about permission rather than exposed as the raw
        verdict, so no call site can accidentally treat "cannot tell" as a
        pass by testing ``verdict is not DIFFERS``.
        """
        return self.verdict is IdentityVerdict.MATCHES


def decide_identity(
    observed: "StoreInstance",
    expected: Optional[str],
    *,
    observed_uuid: Optional[str] = None,
    expected_uuid: Optional[str] = None,
    subject: str = "this connection",
) -> IdentityCheck:
    """Decide identity from ALREADY-PROBED values. Pure; opens nothing.

    ``subject`` is the only thing the two call sites are allowed to differ on —
    it names what reached the store ("this connection" for health, "this
    resolution" for ``resolve_store``) so the message reads correctly without
    the outcomes being re-expressed twice.

    THE FULL TABLE, agreed with dotfiles before either of us wrote a test, and
    exercised row-by-row in ``test__identity_decision_both_halves.py``::

        uuid CORRECT, instance unset      cannot-tell / False
        uuid GARBAGE, instance unset      differs     / False
        uuid unset,   instance CORRECT    cannot-tell / False
        uuid unset,   instance GARBAGE    differs     / False
        both CORRECT                      matches     / True
        uuid GARBAGE, instance CORRECT    differs     / False   <- the 08-17 bug
        uuid CORRECT, instance GARBAGE    differs     / False
        both unset                        cannot-tell / False
        same uuid,    instance DIFFERENT  differs     / False   <- the live fork

    FAIL-CLOSED: if EITHER declared expectation differs, the verdict DIFFERS.
    Two independent expectations cannot be collapsed into one, and a mismatch
    on either half is a mismatch.
    """
    from ._store_instance import Certainty

    # (1) UUID MISMATCH FIRST. Ordering does not change any verdict — a
    #     mismatch on either half is DIFFERS — but it decides which REASON the
    #     reader gets, and "you are on a different board" is the more
    #     actionable of the two when both are wrong.
    if expected_uuid and observed_uuid and observed_uuid != expected_uuid:
        return IdentityCheck(
            verdict=IdentityVerdict.DIFFERS,
            observed=observed,
            expected=expected,
            observed_uuid=observed_uuid,
            expected_uuid=expected_uuid,
            reason=(
                f"{subject} reached a store whose own uuid is "
                f"{observed_uuid!r}, but {expected_uuid!r} was pinned. A "
                "restored or re-bootstrapped database gets a NEW uuid while "
                "keeping the server it runs on, so the instance agreeing is "
                "not evidence. Point the client at the pinned store, or re-pin "
                "deliberately if the move was intended."
            ),
        )

    # (2) INSTANCE MISMATCH. Only meaningful when the store could report one;
    #     an unreadable instance is (4), not a mismatch.
    if expected and observed.certainty is not Certainty.UNKNOWN:
        if observed.instance_id != expected:
            return IdentityCheck(
                verdict=IdentityVerdict.DIFFERS,
                observed=observed,
                expected=expected,
                observed_uuid=observed_uuid,
                expected_uuid=expected_uuid,
                reason=(
                    f"{subject} reached instance {observed.instance_id!r}, but "
                    f"{expected!r} was pinned. Two stores can carry the SAME "
                    "store_uuid and different data — measured 2026-08-12 on "
                    "THREE databases sharing "
                    "1d55dd6e-3d2a-4c24-a429-a78835ab988f — so a matching uuid "
                    "is not evidence. Point $SCITEX_CARDS_DB at the pinned "
                    "store, or re-pin deliberately if the move was intended."
                ),
            )

    # (3) A PINNED UUID THE STORE CANNOT ANSWER. Not a mismatch: unreadable is
    #     not "different", and reporting it as one sends the reader to fix a
    #     configuration that may be correct.
    if expected_uuid and not observed_uuid:
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=expected,
            observed_uuid=observed_uuid,
            expected_uuid=expected_uuid,
            reason=(
                f"a store uuid is pinned ({expected_uuid!r}) but this store "
                "cannot report one, so the halves cannot be compared."
            ),
        )

    # (4) A PINNED INSTANCE THE STORE CANNOT ANSWER.
    if expected and observed.certainty is Certainty.UNKNOWN:
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=expected,
            observed_uuid=observed_uuid,
            expected_uuid=expected_uuid,
            reason=(
                f"an identity is pinned ({expected!r}) but this store cannot "
                f"report one: {observed.reason}"
            ),
        )

    # (5) NOTHING PINNED, OR ONLY ONE HALF PINNED. A half-pin is not a pass:
    #     the instance alone answers which SERVER, the uuid alone answers which
    #     BOARD, and the caller asked about the store.
    if not expected or not expected_uuid:
        missing = []
        if not expected_uuid:
            missing.append("store uuid")
        if not expected:
            missing.append("instance")
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=expected,
            observed_uuid=observed_uuid,
            expected_uuid=expected_uuid,
            reason=(
                f"no expected {' and no expected '.join(missing)} is pinned, "
                f"so {subject} cannot be checked against anything. Record the "
                "identity of the store you trust and pin BOTH halves; the "
                "instance alone says which server and the uuid alone says "
                "which board, and an unpinned client cannot tell a stale "
                "replica from the store it meant to reach."
            ),
        )

    # (6) Both halves pinned, both agree.
    return IdentityCheck(
        verdict=IdentityVerdict.MATCHES,
        observed=observed,
        expected=expected,
        observed_uuid=observed_uuid,
        expected_uuid=expected_uuid,
    )


__all__ = ["IdentityCheck", "IdentityVerdict", "decide_identity"]

# EOF
