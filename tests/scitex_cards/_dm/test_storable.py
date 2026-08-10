#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the DM body storability guard.

Several of these pin defects MEASURED on 2026-07-30 rather than imagined, and
each says which one -- a regression test whose motivating failure is not
written down gets deleted by whoever later finds it puzzling.
"""

from pathlib import Path

import pytest

from scitex_cards._dm.storable import (
    NUL,
    NUL_MARKER,
    to_storable,
    unstorable_offsets,
)

#: A body that mentions the backslash notation AND contains the byte, exactly
#: like live row m_696cd33cc6dc, which is what made the naive marker ambiguous.
AMBIGUOUS_BODY = "the escape is written \\x00 in prose" + NUL + "and the byte"


class TestUnstorableOffsets:
    def test_clean_text_reports_no_offsets(self):
        # Arrange
        text = "an entirely ordinary message"
        # Act
        offsets = unstorable_offsets(text)
        # Assert
        assert offsets == []

    def test_finds_the_single_byte_at_its_index(self):
        # Arrange
        text = "before" + NUL + "after"
        # Act
        offsets = unstorable_offsets(text)
        # Assert
        assert offsets == [6]

    def test_finds_every_occurrence_not_just_the_first(self):
        # Arrange
        text = NUL + "a" + NUL + "b" + NUL
        # Act
        offsets = unstorable_offsets(text)
        # Assert
        assert offsets == [0, 2, 4]

    def test_non_string_input_is_not_this_guards_business(self):
        # Arrange
        not_text = None
        # Act
        offsets = unstorable_offsets(not_text)
        # Assert
        assert offsets == []

    def test_other_control_characters_are_left_alone(self):
        # Arrange: PostgreSQL accepts every C0 control character EXCEPT NUL, so
        # widening this guard would mangle legitimate content for no reason.
        text = "tab\there\nnewline\rreturn\x1bescape\x07bell"
        # Act
        offsets = unstorable_offsets(text)
        # Assert
        assert offsets == []


class TestToStorableCleanPath:
    def test_clean_text_reports_no_replacements(self):
        # Arrange
        text = "an entirely ordinary message"
        # Act
        _result, offsets = to_storable(text)
        # Assert
        assert offsets == []

    def test_clean_text_returns_the_same_object(self):
        # Arrange: ~100% of messages take this path, so it must not allocate.
        text = "an entirely ordinary message"
        # Act
        result, _offsets = to_storable(text)
        # Assert
        assert result is text


class TestToStorableReplacement:
    def test_the_byte_becomes_the_visible_marker(self):
        # Arrange
        text = "before" + NUL + "after"
        # Act
        result, _offsets = to_storable(text)
        # Assert
        assert result == "before" + NUL_MARKER + "after"

    def test_the_replaced_offset_is_reported(self):
        # Arrange
        text = "before" + NUL + "after"
        # Act
        _result, offsets = to_storable(text)
        # Assert
        assert offsets == [6]

    def test_the_result_no_longer_contains_the_byte(self):
        # Arrange
        text = NUL.join(["a", "b", "c"])
        # Act
        result, _offsets = to_storable(text)
        # Assert
        assert NUL not in result

    def test_every_occurrence_is_reported(self):
        # Arrange
        text = NUL.join(["a", "b", "c"])
        # Act
        _result, offsets = to_storable(text)
        # Assert
        assert len(offsets) == 2

    def test_marker_is_a_single_codepoint(self):
        # Arrange: a multi-character marker would shift every later offset,
        # making the reported offsets useless for locating the originals.
        marker = NUL_MARKER
        # Act
        width = len(marker)
        # Assert
        assert width == 1

    def test_offsets_index_the_original_text(self):
        # Arrange
        text = "0123" + NUL + "5678"
        # Act
        _result, offsets = to_storable(text)
        # Assert
        assert text[offsets[0]] == NUL

    def test_offsets_also_locate_the_marker_in_the_result(self):
        # Arrange
        text = "0123" + NUL + "5678"
        # Act
        result, offsets = to_storable(text)
        # Assert
        assert result[offsets[0]] == NUL_MARKER

    def test_length_is_preserved_so_later_offsets_stay_valid(self):
        # Arrange
        text = "0123" + NUL + "5678"
        # Act
        result, _offsets = to_storable(text)
        # Assert
        assert len(result) == len(text)


class TestTheAmbiguityThatMotivatedTheMarkerChoice:
    """MEASURED on live row m_696cd33cc6dc, 2026-07-30.

    The first correction used the four characters backslash-x-0-0 as its
    marker. That body ALREADY contained the sequence twice as prose, so a
    naive un-escape produced three NULs where the original had one. The marker
    must be something prose cannot produce.
    """

    def test_the_source_body_really_does_hold_exactly_one_byte(self):
        # Arrange
        body = AMBIGUOUS_BODY
        # Act
        count = body.count(NUL)
        # Assert
        assert count == 1

    def test_a_backslash_marker_round_trips_to_the_wrong_byte_count(self):
        # Arrange
        naive_marker = "\\x00"
        # Act
        round_tripped = AMBIGUOUS_BODY.replace(NUL, naive_marker).replace(
            naive_marker, NUL
        )
        # Assert
        assert round_tripped.count(NUL) == 2

    def test_a_backslash_marker_does_not_round_trip_at_all(self):
        # Arrange
        naive_marker = "\\x00"
        # Act
        round_tripped = AMBIGUOUS_BODY.replace(NUL, naive_marker).replace(
            naive_marker, NUL
        )
        # Assert
        assert round_tripped != AMBIGUOUS_BODY

    def test_our_marker_round_trips_exactly_on_the_same_input(self):
        # Arrange
        expected = AMBIGUOUS_BODY
        # Act
        result, _offsets = to_storable(AMBIGUOUS_BODY)
        round_tripped = result.replace(NUL_MARKER, NUL)
        # Assert
        assert round_tripped == expected

    def test_our_marker_leaves_the_prose_notation_untouched(self):
        # Arrange
        expected_prose_occurrences = 1
        # Act
        result, _offsets = to_storable(AMBIGUOUS_BODY)
        # Assert
        assert result.count("\\x00") == expected_prose_occurrences


class TestTheGuardModuleIsItselfPlainText:
    """MEASURED, 2026-07-30: the first draft of the guard put a real NUL on
    line 53 by spelling the constant as a quoted literal. Git classifies such
    a file as binary, so every future diff of the guard would be unreviewable
    -- the exact defect the rows that started all this were discussing.
    """

    def test_the_guard_source_contains_no_literal_nul(self):
        # Arrange
        import scitex_cards._dm.storable as mod

        source = Path(mod.__file__)
        # Act
        count = source.read_bytes().count(b"\x00")
        # Assert
        assert count == 0, (
            f"{source} contains a literal NUL: git would treat the guard "
            "module as binary and its diffs would be unreviewable"
        )

    def test_this_test_file_contains_no_literal_nul_either(self):
        # Arrange
        source = Path(__file__)
        # Act
        count = source.read_bytes().count(b"\x00")
        # Assert
        assert count == 0

    def test_the_constant_is_one_codepoint_wide(self):
        # Arrange: guards against a "fix" that makes the file plain text by
        # making the constant wrong -- an empty string would silently disable
        # every check in this file.
        value = NUL
        # Act
        width = len(value)
        # Assert
        assert width == 1

    def test_the_constant_is_actually_the_nul_codepoint(self):
        # Arrange
        value = NUL
        # Act
        codepoint = ord(value)
        # Assert
        assert codepoint == 0


class TestRejectionIsNotTheDesign:
    def test_a_long_body_that_quotes_the_byte_keeps_its_length(self):
        # Arrange: the dm_messages row was a legitimate 4 KB technical report.
        # Rejecting would have destroyed it.
        body = "x" * 4000 + NUL + "y" * 100
        # Act
        result, _offsets = to_storable(body)
        # Assert
        assert len(result) == len(body)

    def test_every_other_character_survives_untouched(self):
        # Arrange
        body = "x" * 4000 + NUL + "y" * 100
        # Act
        result, _offsets = to_storable(body)
        # Assert
        assert result.replace(NUL_MARKER, "") == body.replace(NUL, "")

    @pytest.mark.parametrize(
        "awkward",
        ["", NUL, NUL * 50, "\U0001f600" + NUL, "a" * 10000 + NUL],
        ids=["empty", "bare", "many", "after-astral", "long"],
    )
    def test_no_string_survives_with_the_byte_still_in_it(self, awkward):
        # Arrange
        text = awkward
        # Act
        result, _offsets = to_storable(text)
        # Assert
        assert NUL not in result


@pytest.mark.parametrize(
    "text,expected_count",
    [("clean", 0), (NUL, 1), ("a" + NUL + "b" + NUL, 2), (NUL * 5, 5)],
    ids=["clean", "one", "two", "five"],
)
def test_offsets_count_matches_occurrences(text, expected_count):
    # Arrange
    subject = text
    # Act
    offsets = unstorable_offsets(subject)
    # Assert
    assert len(offsets) == expected_count


# EOF
