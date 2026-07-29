#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Everything our templates load from scitex-ui must actually ARRIVE.

THE FAILURE MODE THIS EXISTS FOR IS SILENT BY CONSTRUCTION. Both pages consume
scitex-ui through progressive enhancement::

    if (window.STX && window.STX.Combobox) { ...enhance... } else { ...plain... }

which is the right shape — a missing base component must not take the page down.
But it means an asset that never arrives produces NO error, NO console warning
and NO visual difference the author would recognise: the page keeps working, in
its degraded branch, forever. Nothing on either side fails, so nothing gets
reported, so the enhancement is simply absent for as long as nobody thinks to
look. On 2026-07-29 an investigation was opened on the belief that the board's
Combobox had been inert for weeks behind a too-low version floor; it had not
been, but the reason nobody could say so without a browser is exactly this.

WHAT IS PINNED, and why each half is needed:

  1. Every ``{% static 'scitex_ui/...' %}`` path our templates reference RESOLVES
     through the real staticfiles finders. This catches the version-floor and
     asset-rename classes: a scitex-ui that does not ship the file at all.

  2. The bundle we feature-detect actually ATTACHES ``window.STX.Combobox`` and
     its ``fuzzyMatch`` static. Presence is not currency — and this is not
     hypothetical. On 2026-07-29 scitex-ui regenerated ``js/app/combobox.js``
     from its TypeScript with ``esbuild --format=esm``. The result was valid
     JavaScript, byte-for-byte a real file at the same path, passing every
     exists() check — and it set NO GLOBAL, which would have pinned every
     consumer to its fallback branch permanently. Check (1) alone would have
     been green through all of it.

This is a CONTRACT test against the installed scitex-ui, so it fails when the
environment is wrong rather than when this repo is. That is deliberate: an
install too old to honour our declared floor is a real defect, and the declared
floor is the thing this test makes enforceable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scitex_cards._django import views

_TEMPLATES = Path(views.__file__).parent / "templates" / "scitex_cards"

#: `{% static 'scitex_ui/...' %}` with either quote style. Only scitex-ui paths:
#: our own assets ship in this wheel and are covered by the packaging tests.
_STATIC = re.compile(r"""\{%\s*static\s+['"](scitex_ui/[^'"]+)['"]\s*%\}""")

#: Hand-written IIFE bundles from scitex-ui that MUST attach a global, mapped to
#: the symbol our code feature-detects. Extend this when a page starts consuming
#: another one. A bundle absent from this map is not "exempt" — it is merely not
#: yet depended on by a `window.`-guarded branch in this repo.
_GLOBAL_BUNDLES = {
    "scitex_ui/js/app/combobox.js": "Combobox",
}


def _referenced_assets() -> dict[str, list[str]]:
    """Map scitex-ui static path -> templates that reference it."""
    found: dict[str, list[str]] = {}
    for template in sorted(_TEMPLATES.glob("*.html")):
        for path in _STATIC.findall(template.read_text(encoding="utf-8")):
            found.setdefault(path, []).append(template.name)
    return found


@pytest.fixture(scope="module")
def referenced() -> dict[str, list[str]]:
    return _referenced_assets()


# === positive control ======================================================
#
# A scan that quietly matches nothing reports "all assets resolve" and means
# "I examined no assets". Those two read identically in a green run, so the
# scan's own reach is asserted before anything is concluded from it.


def test_the_scan_finds_scitex_ui_references_at_all(referenced) -> None:
    """If this goes red, every other test in this file is vacuous."""
    # Arrange / Act / Assert
    assert referenced, (
        "no `{% static 'scitex_ui/...' %}` references found under "
        f"{_TEMPLATES} — the regex, not the assets, is what broke"
    )


def test_the_scan_covers_both_operator_facing_pages(referenced) -> None:
    """board_v3 and chat both consume scitex-ui; a scan seeing one is half-blind."""
    # Arrange
    seen = {name for names in referenced.values() for name in names}
    # Act / Assert
    assert {"board_v3.html", "chat.html"} <= seen


def test_the_combobox_bundle_is_among_them(referenced) -> None:
    """The specific asset the fuzzy-filter enhancement rides on."""
    # Arrange / Act / Assert
    assert "scitex_ui/js/app/combobox.js" in referenced


# === (1) the files arrive ==================================================


def test_every_referenced_scitex_ui_asset_resolves(referenced) -> None:
    """Through the REAL finders, against the INSTALLED scitex-ui."""
    # Arrange
    from django.contrib.staticfiles import finders

    # Act
    missing = {
        path: users for path, users in referenced.items() if finders.find(path) is None
    }
    # Assert
    assert not missing, (
        "scitex-ui assets referenced by our templates but NOT shipped by the "
        f"installed scitex-ui: {json.dumps(missing, indent=2)} — the page will "
        "silently fall back rather than fail, so nothing else will tell you"
    )


# === (2) the symbol arrives ================================================


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _globals_attached_by(asset: str) -> list[str]:
    """Execute the bundle with a bare `window` and report what it attached.

    Runs the file scitex-ui actually installed — the point is to observe the
    shipped artifact's effect, which is not derivable from its path.
    """
    from django.contrib.staticfiles import finders

    resolved = finders.find(asset)
    assert resolved is not None, f"{asset} is not installed"
    script = (
        "const fs = require('fs');\n"
        "const vm = require('vm');\n"
        "const window = {};\n"
        "const ctx = vm.createContext({ window, self: window, "
        "document: undefined, console });\n"
        f"vm.runInContext(fs.readFileSync({json.dumps(resolved)}, 'utf8'), ctx);\n"
        "console.log(JSON.stringify("
        "window.STX ? Object.keys(window.STX) : []));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(proc.stdout.strip())


@pytest.mark.parametrize(("asset", "symbol"), sorted(_GLOBAL_BUNDLES.items()))
def test_the_bundle_attaches_the_symbol_we_feature_detect(
    asset: str, symbol: str
) -> None:
    """`window.STX.<symbol>` — the exact expression our `if` guards evaluate."""
    # Arrange / Act
    attached = _globals_attached_by(asset)
    # Assert
    assert symbol in attached, (
        f"{asset} installed but attaches window.STX = {attached!r} — an ESM or "
        f"module-scoped build sets no global, so every `window.STX.{symbol}` "
        "guard in this repo takes its fallback branch and nothing reports it"
    )


def test_the_combobox_exposes_the_fuzzy_matcher_as_a_static() -> None:
    """`Combobox.fuzzyMatch` is what chat_filter.js consumes for list filtering.

    A list filter wants the MATCHER, not the widget. If scitex-ui ever drops the
    static, this page silently degrades to substring matching and the operator
    meets two different search behaviours in one app.
    """
    # Arrange
    from django.contrib.staticfiles import finders

    resolved = finders.find("scitex_ui/js/app/combobox.js")
    assert resolved is not None
    # Act
    script = (
        "const fs = require('fs');\n"
        "const vm = require('vm');\n"
        "const window = {};\n"
        "const ctx = vm.createContext({ window, self: window, console });\n"
        f"vm.runInContext(fs.readFileSync({json.dumps(resolved)}, 'utf8'), ctx);\n"
        "const cb = window.STX && window.STX.Combobox;\n"
        "console.log(JSON.stringify({\n"
        "  isFunction: typeof (cb && cb.fuzzyMatch) === 'function',\n"
        "  subsequence: !!(cb && cb.fuzzyMatch "
        "&& cb.fuzzyMatch('dvhlp', 'dev-helper')),\n"
        "  rejects: !!(cb && cb.fuzzyMatch "
        "&& cb.fuzzyMatch('zzz', 'dev-helper')),\n"
        "}));\n"
    )
    out = json.loads(
        subprocess.run(
            [_node(), "--input-type=commonjs", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    )
    # Assert
    assert out == {"isFunction": True, "subsequence": True, "rejects": False}
