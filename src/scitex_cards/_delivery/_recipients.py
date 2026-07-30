#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-user delivery config + the delivery POLICY gate.

Reads ``<store_dir>/recipients.json`` (a sibling of the task store), which
maps each user to the channels they should be delivered on::

    {"users": {
       "u_3f9a1c0b7e42": {"channels": [
         {"kind": "log"},
         {"kind": "telegram", "address": "123456789"}]},
       "dave": {"channels": [{"kind": "log"}]}}}

``address`` is OPTIONAL (the ``log`` channel needs none). A missing file
yields an empty recipient set (no crash) — delivery simply has nothing to
do. Resolution follows the same store precedence via
:func:`scitex_cards._inbox._resolved_store`.

Policy lives HERE, never inside a channel: :func:`should_deliver_now` is the
SEAM where quiet-hours / consent / rate-limit will hang off. Slice 1 returns
True (no-op), but the loop MUST call it and a False result yields a
``skipped`` outcome (re-evaluated next run, never terminal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .._inbox import _resolved_store

#: Recipients config filename, a sibling of the task store.
RECIPIENTS_FILENAME = "recipients.json"


@dataclass(frozen=True)
class ChannelConfig:
    """One configured channel for a user (a ``{kind, address?}`` entry)."""

    kind: str
    address: str = ""


@dataclass(frozen=True)
class Recipient:
    """A user plus the ordered list of channels to deliver to them on."""

    user: str
    channels: list[ChannelConfig] = field(default_factory=list)


def recipients_path(store: str | Path | None = None) -> Path:
    """Resolve ``<store_dir>/recipients.json`` for the resolved store."""
    return _resolved_store(store).parent / RECIPIENTS_FILENAME


def _load_recipients_doc(store: str | Path | None) -> dict:
    """Load the raw recipients mapping from ``recipients.json``.

    Absent/unreadable/malformed → ``{}``.
    """
    import json

    path = recipients_path(store)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_channels(raw: object) -> list[ChannelConfig]:
    """Coerce a raw ``channels:`` value into a list of ``ChannelConfig``.

    Defensive: a non-list, or a row missing/empty ``kind``, is skipped so a
    single malformed entry never breaks the whole recipient set.
    """
    if not isinstance(raw, list):
        return []
    out: list[ChannelConfig] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        address = row.get("address")
        out.append(
            ChannelConfig(
                kind=kind,
                address=str(address) if address is not None else "",
            )
        )
    return out


def load_recipients(store: str | Path | None = None) -> list[Recipient]:
    """Return every configured recipient (missing file → ``[]``).

    Reads ``recipients.json``. A missing file, an absent/non-mapping
    ``users:`` key, or a user with no usable
    channels all degrade gracefully — a user with zero channels is dropped
    (nothing to deliver), never an error. Deterministic order: users are
    returned sorted by id so a delivery run is reproducible.
    """
    data = _load_recipients_doc(store)
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, dict):
        return []
    out: list[Recipient] = []
    for user_id in sorted(users):
        if not isinstance(user_id, str) or not user_id:
            continue
        cfg = users[user_id]
        channels = _parse_channels(
            cfg.get("channels") if isinstance(cfg, dict) else None
        )
        if not channels:
            continue
        out.append(Recipient(user=user_id, channels=channels))
    return out


def should_deliver_now(user: str, notification: dict) -> bool:
    """Policy gate: may we deliver ``notification`` to ``user`` right now?

    THE seam for quiet-hours / consent / rate-limiting. Slice 1 is a no-op
    that always returns True, but the loop ALWAYS calls it and a False
    result produces a ``skipped`` outcome (the item is re-evaluated next
    run, never marked terminal). Policy belongs HERE so channels stay dumb
    transports — a channel must never decide whether to deliver.
    """
    _ = (user, notification)  # reserved for real policy in a later slice.
    return True


__all__ = [
    "ChannelConfig",
    "RECIPIENTS_FILENAME",
    "Recipient",
    "load_recipients",
    "recipients_path",
    "should_deliver_now",
]

# EOF
