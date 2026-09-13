#!/usr/bin/env python3
"""Browser delivery labels remain deterministic and distinguish acceptance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_MODULE = (
    Path(__file__).resolve().parents[4]
    / "src/scitex_cards/_django/static/scitex_cards/chat/chat_exchange.js"
)


def _call(expression: str):
    source = (
        f"const x = require({json.dumps(str(_MODULE))}); "
        f"console.log(JSON.stringify({expression}));"
    )
    completed = subprocess.run(
        ["node", "-e", source], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_accepted_exchange_is_not_labelled_delivered() -> None:
    # Arrange
    exchange = {"final": False, "status": {"code": 202}}
    # Act
    label = _call(f"x.labelFor({json.dumps(exchange)})")
    # Assert
    assert label == "Accepted"


def test_final_success_is_labelled_delivered() -> None:
    # Arrange
    exchange = {"final": True, "status": {"code": 200}}
    # Act
    label = _call(f"x.labelFor({json.dumps(exchange)})")
    # Assert
    assert label == "Delivered"


def test_final_failure_is_labelled_failed() -> None:
    # Arrange
    exchange = {"final": True, "status": {"code": 503}}
    # Act
    label = _call(f"x.labelFor({json.dumps(exchange)})")
    # Assert
    assert label == "Delivery failed"


# EOF
