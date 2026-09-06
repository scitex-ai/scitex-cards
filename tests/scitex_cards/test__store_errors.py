#!/usr/bin/env python3
"""The store refusal must be loud to us and quiet to strangers.

FOUND BY scitex-hub, in a real browser, as an ANONYMOUS visitor to
https://scitex.ai/apps/cards/ : the page body carried the absolute container path
``/app/.scitex/cards/cards.db`` plus a paragraph of internal design rationale.

The verbose text is NOT the defect - views.py's own comment records that an
earlier version let the refusal escape as an unparseable HTML page, so the board
showed a bare "HTTP 500" with no cause, and the store's sentence in the body is
what makes an outage diagnosable. The defect is that there was only ONE audience.
So these tests pin both directions: the detail survives for us, and the path does
not reach a stranger.
"""

from _banned import DRIVER, ENGINE  # noqa: F401

import pytest

from scitex_cards._store_errors import StoreUnavailableError

_DETAIL = (
    "canonical store /app/.scitex/cards/cards.db does not exist. REFUSING to "
    "continue: the exporter answers a missing database with an empty document."
)


# === the detail is preserved for us ======================================


def test_the_full_detail_is_the_exception_text():
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    text = str(exc)

    # Assert
    assert text == _DETAIL


def test_it_is_still_a_runtimeerror_so_existing_handlers_keep_catching_it():
    """Introducing the type must break no caller: every `except Exception`, and
    every `except RuntimeError`, has to keep working unchanged."""
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    caught = isinstance(exc, RuntimeError)

    # Assert
    assert caught is True


# === the public form is safe =============================================


def test_the_public_summary_never_contains_a_path():
    """THE TEST. If this fails, an anonymous visitor learns our layout."""
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    summary = exc.public_summary

    # Assert
    assert "/" not in summary, f"public summary leaks a path: {summary!r}"


def test_the_public_summary_does_not_echo_the_detail():
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    summary = exc.public_summary

    # Assert
    assert _DETAIL not in summary


def test_the_public_summary_says_something_useful():
    """A blank string would pass the leak tests and tell the visitor nothing."""
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    summary = exc.public_summary

    # Assert
    assert len(summary.strip()) >= 20


def test_the_public_summary_mentions_no_internal_vocabulary():
    """'canonical', 'exporter', 'SCITEX_CARDS_DB' are ours, not theirs."""
    # Arrange
    exc = StoreUnavailableError(_DETAIL)

    # Act
    lowered = exc.public_summary.lower()

    # Assert
    leaked = [
        w
        for w in ("canonical", "exporter", "scitex_cards_db", ENGINE)
        if w in lowered
    ]
    assert leaked == [], f"internal vocabulary in public summary: {leaked}"


def test_a_caller_may_override_the_summary():
    """Different refusals may warrant different public wording."""
    # Arrange
    exc = StoreUnavailableError(_DETAIL, public_summary="Sign in to see your board.")

    # Act
    summary = exc.public_summary

    # Assert
    assert summary == "Sign in to see your board."


# === the raise site actually uses the type ===============================


def test_the_missing_store_refusal_raises_this_type(new_store):
    """A perfect exception class nobody raises protects nothing.

    Checks the TYPE at a real raise site, not the message - the message is what
    we just stopped depending on.

    WHAT "MISSING" MEANS CHANGED, and pointing at the old kind of missing now
    tests the wrong door. This used to set the ambient store to a path that did
    not exist. A path is refused before any store logic runs, with
    `UnrecognisedStoreTarget` - a plain RuntimeError, NOT a StoreUnavailableError
    - so the test measured the target guard rather than the missing-store
    refusal it is named for.

    The modern equivalent is a store that is REACHABLE BUT UNPROVISIONED, which
    is what `bootstrap=False` hands out and what a fresh deployment looks like.
    That path raises StoreNotProvisionedError, which IS a StoreUnavailableError,
    so the assertion below is unchanged and still proves the class is used
    somewhere real.
    """
    # Arrange
    import os

    from scitex_cards import _store_canonical_read

    saved = os.environ.get("SCITEX_CARDS_DB")
    os.environ["SCITEX_CARDS_DB"] = new_store("errors_unprovisioned", bootstrap=False)

    # Act
    try:
        _store_canonical_read._read_canonical_db_or_raise()
        raised = None
    except StoreUnavailableError as exc:
        raised = exc
    except Exception as exc:  # a different type is a real failure of this test
        raised = exc
    finally:
        if saved is None:
            os.environ.pop("SCITEX_CARDS_DB", None)
        else:
            os.environ["SCITEX_CARDS_DB"] = saved

    # Assert
    assert isinstance(raised, StoreUnavailableError), f"got {type(raised).__name__}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
