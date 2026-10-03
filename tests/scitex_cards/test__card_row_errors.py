"""The additive typed row refusal contracts contain no private payload data."""
import pytest


@pytest.fixture
def errors():
    from scitex_cards import _card_row_errors
    return _card_row_errors


def test_absence_refusal_names_the_observed_revision(errors):
    # Arrange
    task_id = "fixture-card"
    # Act
    refusal = errors.CardAlreadyExistsError(task_id, 7)
    # Assert
    assert refusal.found == 7


def test_payload_shape_refusal_is_an_export_refusal(errors):
    from scitex_cards._db_export import ExportRefused
    # Arrange
    task_id = "fixture-card"
    # Act
    refusal = errors.CardPayloadShapeError(task_id)
    # Assert
    assert isinstance(refusal, ExportRefused)


def test_payload_shape_refusal_has_a_fixed_public_summary(errors):
    # Arrange
    task_id = "fixture-card"
    # Act
    refusal = errors.CardPayloadShapeError(task_id)
    # Assert
    assert refusal.public_summary == "The canonical card payload is unreadable."
