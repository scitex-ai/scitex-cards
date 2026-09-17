"""Bounded, cursor-paginated response contract for the MCP task list."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 200
MAX_PAGE_ITEM_BYTES = 1_000_000
_CURSOR_PREFIX = "cards-list-v1."


class ListTasksPageMeta(BaseModel):
    """Stable pagination metadata returned with every MCP task-list page."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    returned: int
    total: int
    truncated: bool
    next_cursor: str | None
    item_bytes: int
    max_item_bytes: int = MAX_PAGE_ITEM_BYTES


class ListTasksPage(BaseModel):
    """The versioned, static response shape of the MCP ``list_tasks`` tool."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scitex.cards.list_tasks.page.v1"] = (
        "scitex.cards.list_tasks.page.v1"
    )
    items: list[dict[str, Any]]
    page: ListTasksPageMeta


class _CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    offset: int = Field(ge=0)
    query: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    total: int = Field(ge=0)


def _stable_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _encode_cursor(payload: _CursorPayload) -> str:
    raw = payload.model_dump_json().encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    checksum = hashlib.sha256(raw).hexdigest()[:16]
    return f"{_CURSOR_PREFIX}{token}.{checksum}"


def _decode_cursor(cursor: str) -> _CursorPayload:
    message = (
        "cursor must be an unmodified next_cursor returned by list_tasks; "
        "restart from the first page by omitting cursor"
    )
    if (
        not cursor.startswith(_CURSOR_PREFIX)
        or "." not in cursor[len(_CURSOR_PREFIX) :]
    ):
        raise ValueError(message)
    token, checksum = cursor[len(_CURSOR_PREFIX) :].rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if hashlib.sha256(raw).hexdigest()[:16] != checksum:
            raise ValueError(message)
        return _CursorPayload.model_validate_json(raw)
    except (ValueError, ValidationError, UnicodeError) as exc:
        raise ValueError(message) from exc


def paginate_task_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    cursor: str | None,
    query: dict[str, Any],
) -> ListTasksPage:
    """Build one bounded page and refuse stale or cross-query cursors."""

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(f"limit must be an integer from 1 to {MAX_PAGE_LIMIT}")
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be from 1 to {MAX_PAGE_LIMIT}; received {limit}")

    query_digest = _stable_digest(query)
    snapshot_digest = _stable_digest([row.get("id") for row in rows])
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor:
            raise ValueError(
                "cursor must be a non-empty next_cursor returned by list_tasks"
            )
        decoded = _decode_cursor(cursor)
        if decoded.query != query_digest:
            raise ValueError(
                "cursor belongs to different list_tasks filters; omit cursor to "
                "start a new filtered query"
            )
        if decoded.total != len(rows) or decoded.snapshot != snapshot_digest:
            raise ValueError(
                "task results changed during pagination; omit cursor and restart "
                "from the first page"
            )
        offset = decoded.offset

    items: list[dict[str, Any]] = []
    item_bytes = 0
    for row in rows[offset : offset + limit]:
        row_bytes = len(
            json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
        )
        if row_bytes > MAX_PAGE_ITEM_BYTES:
            task_id = row.get("id", "<unknown>")
            raise ValueError(
                f"task {task_id!r} is {row_bytes} bytes and exceeds the MCP page "
                f"limit of {MAX_PAGE_ITEM_BYTES} bytes; request it explicitly "
                "with get_task"
            )
        if items and item_bytes + row_bytes > MAX_PAGE_ITEM_BYTES:
            break
        items.append(row)
        item_bytes += row_bytes

    next_offset = offset + len(items)
    truncated = next_offset < len(rows)
    next_cursor = None
    if truncated:
        next_cursor = _encode_cursor(
            _CursorPayload(
                version=1,
                offset=next_offset,
                query=query_digest,
                snapshot=snapshot_digest,
                total=len(rows),
            )
        )
    return ListTasksPage(
        items=items,
        page=ListTasksPageMeta(
            limit=limit,
            returned=len(items),
            total=len(rows),
            truncated=truncated,
            next_cursor=next_cursor,
            item_bytes=item_bytes,
        ),
    )


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_ITEM_BYTES",
    "MAX_PAGE_LIMIT",
    "ListTasksPage",
    "paginate_task_rows",
]
