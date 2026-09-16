"""Regression coverage for the bounded MCP ``list_tasks`` response."""

from __future__ import annotations

import asyncio
import json

import pytest

from scitex_cards._mcp_list_page import (
    MAX_PAGE_ITEM_BYTES,
    ListTasksPage,
    paginate_task_rows,
)


def _page(rows, *, limit=100, cursor=None, query=None):
    return paginate_task_rows(
        rows,
        limit=limit,
        cursor=cursor,
        query=query or {"agent": None},
    )


def test_large_unfiltered_result_is_bounded_and_explicitly_truncated():
    # Arrange
    rows = [{"id": f"card-{index:05d}", "title": "x" * 8_000} for index in range(7_732)]

    # Act
    page = _page(rows)
    encoded = page.model_dump_json().encode()
    observed = {
        "total": page.page.total,
        "truncated": page.page.truncated,
        "has_cursor": page.page.next_cursor is not None,
        "returned_is_bounded": page.page.returned <= 100,
        "items_are_bounded": page.page.item_bytes <= MAX_PAGE_ITEM_BYTES,
        "response_is_bounded": len(encoded) < MAX_PAGE_ITEM_BYTES + 100_000,
    }

    # Assert
    assert observed == {
        "total": 7_732,
        "truncated": True,
        "has_cursor": True,
        "returned_is_bounded": True,
        "items_are_bounded": True,
        "response_is_bounded": True,
    }


def test_cursor_returns_each_item_once_in_stable_pages():
    # Arrange
    rows = [{"id": f"card-{index}"} for index in range(5)]

    # Act
    first = _page(rows, limit=2)
    second = _page(rows, limit=2, cursor=first.page.next_cursor)
    third = _page(rows, limit=2, cursor=second.page.next_cursor)
    ids = [row["id"] for page in (first, second, third) for row in page.items]

    # Assert
    assert (ids, third.page.truncated, third.page.next_cursor) == (
        ["card-0", "card-1", "card-2", "card-3", "card-4"],
        False,
        None,
    )


def test_cursor_rejects_different_filters_with_helpful_message():
    # Arrange
    first = _page([{"id": "a"}, {"id": "b"}], limit=1, query={"agent": "a"})

    # Act
    # Assert
    with pytest.raises(ValueError, match="different list_tasks filters"):
        _page(
            [{"id": "a"}, {"id": "b"}],
            limit=1,
            cursor=first.page.next_cursor,
            query={"agent": "b"},
        )


def test_cursor_rejects_changed_result_snapshot_with_helpful_message():
    # Arrange
    first = _page([{"id": "a"}, {"id": "b"}], limit=1)

    # Act
    # Assert
    with pytest.raises(ValueError, match="results changed during pagination"):
        _page(
            [{"id": "a"}, {"id": "c"}],
            limit=1,
            cursor=first.page.next_cursor,
        )


@pytest.mark.parametrize("cursor", ["garbage", "cards-list-v1.not-base64.bad"])
def test_malformed_cursor_has_one_static_actionable_message(cursor):
    # Arrange
    rows = [{"id": "a"}]

    # Act
    # Assert
    with pytest.raises(
        ValueError,
        match="cursor must be an unmodified next_cursor returned by list_tasks",
    ):
        _page(rows, cursor=cursor)


@pytest.mark.parametrize("limit", [0, 201, True, "10"])
def test_limit_validation_is_helpful(limit):
    # Arrange
    rows = [{"id": "a"}]

    # Act
    # Assert
    with pytest.raises(ValueError, match="limit must"):
        _page(rows, limit=limit)


def test_single_oversized_task_fails_loud_instead_of_returning_an_empty_page():
    # Arrange
    rows = [{"id": "oversized", "title": "x" * (MAX_PAGE_ITEM_BYTES + 1)}]

    # Act
    # Assert
    with pytest.raises(ValueError, match="request it explicitly with get_task"):
        _page(rows)


def test_response_model_forbids_shape_drift():
    # Arrange
    expected = {
        "top": {"schema_version", "items", "page"},
        "page": {
            "limit",
            "returned",
            "total",
            "truncated",
            "next_cursor",
            "item_bytes",
            "max_item_bytes",
        },
        "version": "scitex.cards.list_tasks.page.v1",
    }

    # Act
    raw = _page([{"id": "a"}]).model_dump_json()
    payload = json.loads(raw)
    observed = {
        "top": set(payload),
        "page": set(payload["page"]),
        "version": ListTasksPage.model_validate_json(raw).schema_version,
    }

    # Assert
    assert observed == expected


def test_real_mcp_schema_exposes_pydantic_limit_contract():
    # Arrange
    from scitex_cards._mcp_server import mcp

    # Act
    tool = asyncio.run(mcp.get_tool("list_tasks"))
    limit = tool.parameters["properties"]["limit"]

    # Assert
    assert limit == {
        "default": 100,
        "description": "Maximum tasks in this page (1-200; default 100).",
        "maximum": 200,
        "minimum": 1,
        "type": "integer",
    }


def test_real_mcp_dispatch_rejects_invalid_limit_before_store_access():
    # Arrange
    from fastmcp.exceptions import ValidationError

    from scitex_cards._mcp_server import mcp

    async def call_invalid_limit():
        await mcp.call_tool("list_tasks", {"limit": 0})

    # Act
    # Assert
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        asyncio.run(call_invalid_limit())
