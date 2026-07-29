#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The compose send path must not latch after the first message.

The defect these pin (2026-07-29, reported by the operator within minutes of
the deploy): `sending` was set true before the POST and released nowhere, so
the FIRST send disabled the composer for the life of the page. Switching DM
peers did not help — the flag is module-level — and only a full reload cleared
it: 「毎回 Ctrl + Shift + R しています」「DM の相手を変えても send できない」.

Each test drives the REAL module under node against stubs, so a regression in
the shipped file fails here rather than in the operator's browser.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_send.js"
)

#: A DOM/fetch harness small enough to read: the module only touches a form, a
#: textarea, a button and fetch, so the stub implements exactly those.
HARNESS = """
globalThis.window = globalThis;

function el(extra) {
  const o = Object.assign(
    { value: "", disabled: false, _handlers: {},
      addEventListener(k, fn) { this._handlers[k] = fn; },
      focus() {}, requestSubmit() { if (this._handlers.submit)
        this._handlers.submit({ preventDefault() {} }); } },
    extra || {}
  );
  return o;
}

globalThis.__mk = function (opts) {
  const form = el(), body = el(), send = el();
  const calls = [];
  const fetchImpl = function (url, init) {
    calls.push({ url, body: JSON.parse(init.body).body });
    return opts.ok
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      : Promise.resolve({ ok: false, status: 500,
                          json: () => Promise.resolve({ error: "boom" }) });
  };
  const api = window.ChatSend.mount({
    form: form, textarea: body, send: send,
    apiBase: "/api", getPeer: () => "sac",
    fetchImpl: fetchImpl,
    clearError() {}, showError() {},
  });
  return { form, body, send, calls, api };
};
"""


def _run(script: str) -> dict:
    """Execute the real module plus ``script`` under node; return its JSON."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment guard
        pytest.skip("node is not installed, so the shipped module cannot run")
    src = MODULE.read_text(encoding="utf-8")
    proc = subprocess.run(
        [node, "-e", src + HARNESS + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:  # pragma: no cover - surfaced as a failure
        raise AssertionError(f"node failed: {proc.stderr[:2000]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_second_message_sends_after_the_first_succeeds():
    """The whole bug: one successful send must not disable the composer."""
    # Arrange
    script = """
    const h = __mk({ ok: true });
    h.body.value = "first";
    Promise.resolve(h.api.send({ preventDefault() {} })).then(() => {
      h.body.value = "second";
      return h.api.send({ preventDefault() {} });
    }).then(() => {
      console.log(JSON.stringify({ bodies: h.calls.map((c) => c.body) }));
    });
    """
    # Act
    out = _run(script)
    # Assert
    assert out["bodies"] == ["first", "second"], (
        "the second send never reached fetch — the re-entry guard latched, "
        "which is exactly the reload-every-time defect"
    )


def test_a_second_message_sends_after_the_first_fails():
    """A FAILED send must release the guard too, or one error bricks the page."""
    # Arrange
    script = """
    const h = __mk({ ok: false });
    h.body.value = "first";
    Promise.resolve(h.api.send({ preventDefault() {} })).then(() => {
      h.body.value = "second";
      return h.api.send({ preventDefault() {} });
    }).then(() => {
      console.log(JSON.stringify({ bodies: h.calls.map((c) => c.body) }));
    });
    """
    # Act
    out = _run(script)
    # Assert
    assert out["bodies"] == ["first", "second"], (
        "after a failed send the guard stayed set, so the composer was dead "
        "for the rest of the page's life"
    )


def test_a_failed_send_puts_the_text_back():
    """The optimistic clear must be undone, or a failure eats what was typed."""
    # Arrange
    script = """
    const h = __mk({ ok: false });
    h.body.value = "please do not lose me";
    Promise.resolve(h.api.send({ preventDefault() {} })).then(() => {
      console.log(JSON.stringify({ value: h.body.value }));
    });
    """
    # Act
    out = _run(script)
    # Assert
    assert out["value"] == "please do not lose me", (
        "a failed send discarded the operator's text; it is cleared "
        "optimistically before the POST and must be restored when it fails"
    )


def test_the_send_button_is_re_enabled_after_a_send():
    """The button half of the guard must release alongside the flag."""
    # Arrange
    script = """
    const h = __mk({ ok: true });
    h.body.value = "x";
    Promise.resolve(h.api.send({ preventDefault() {} })).then(() => {
      console.log(JSON.stringify({ disabled: h.send.disabled }));
    });
    """
    # Act
    out = _run(script)
    # Assert
    assert out["disabled"] is False, "the send button stayed disabled"


def test_a_repeated_submit_during_the_round_trip_sends_once():
    """The guard must still do its original job: no duplicate on Enter-mash."""
    # Arrange
    script = """
    const h = __mk({ ok: true });
    h.body.value = "once";
    const first = h.api.send({ preventDefault() {} });
    h.api.send({ preventDefault() {} });
    h.api.send({ preventDefault() {} });
    Promise.resolve(first).then(() => {
      console.log(JSON.stringify({ n: h.calls.length }));
    });
    """
    # Act
    out = _run(script)
    # Assert
    assert out["n"] == 1, "Enter-mash re-sent the same text — the guard is gone"


# EOF
