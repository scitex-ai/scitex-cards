"""Actual TS/React presentation controls; no mounted HTTP/SDK acceptance claim."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_SRC = _ROOT / "src/scitex_cards/_django/frontend/src"
_BASE = "65d751e7f6cedf92abdd4e9e1c53aaa477c94e88"
_REL_BOARD = "src/scitex_cards/_django/frontend/src/CardsBoard.tsx"


def _dependency_root() -> Path:
    for root in (_ROOT, *_ROOT.parents):
        path = root / "src/scitex_cards/_django/frontend/node_modules"
        if (path / "typescript/lib/typescript.js").is_file():
            return path
    pytest.fail("Existing frontend TypeScript/React dependency tree is required")


def _node(script: str, payload: dict) -> str:
    node = shutil.which("node")
    if not node:
        pytest.fail("Existing Node executable is required")
    result = subprocess.run(
        [node, "-e", script, str(_dependency_root())],
        input=json.dumps(payload), capture_output=True, text=True, timeout=7,
    )
    if result.returncode:
        pytest.fail(result.stderr or result.stdout)
    return result.stdout


_AST = r"""
const fs = require('fs');
const ts = require(process.argv[1] + '/typescript');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const parse = (text) => ts.createSourceFile('source.tsx', text,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const sf = parse(p.source);
const all = [];
const walk = n => { all.push(n); ts.forEachChild(n, walk); };
walk(sf);
const printer = ts.createPrinter({removeComments: true});
const print = n => printer.printNode(ts.EmitHint.Unspecified, n, sf);
let result;
if (p.mode === 'transport') {
  const call = all.find(n => ts.isCallExpression(n) &&
    n.expression.getText(sf) === 'createElement' &&
    n.arguments[0]?.getText(sf) === 'CardsBoard');
  const prop = call?.arguments[1]?.properties?.find(n =>
    n.name?.getText(sf) === 'internalChromeEnabled');
  if (!prop) throw Error('Actual mount has no internalChromeEnabled transport');
  result = prop.initializer.getText(sf);
} else if (p.mode === 'bands') {
  const tags = all.filter(n => ts.isJsxSelfClosingElement(n) &&
    n.tagName.getText(sf) === 'LeafHeader');
  const props = tags.map(n => n.attributes.properties.find(a =>
    a.name?.getText(sf) === 'internalChromeEnabled')?.initializer
    ?.expression?.getText(sf) ?? null);
  const defaults = all.filter(n => ts.isBindingElement(n) &&
    n.name.getText(sf) === 'internalChromeEnabled').map(n =>
    n.initializer?.getText(sf) ?? null);
  const gate = all.some(n => ts.isBinaryExpression(n) &&
    n.left.getText(sf) === 'internalChromeEnabled' &&
    n.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken &&
    n.right.getText(sf).includes('graph.store_path'));
  result = {props, defaults, gate};
} else if (p.mode === 'titles') {
  const functions = all.filter(n => ts.isFunctionDeclaration(n) &&
    ['ViewToggle', 'Progress'].includes(n.name?.text));
  result = [];
  const titles = n => {
    if (ts.isJsxAttribute(n) && n.name.text === 'title' &&
        n.initializer && ts.isStringLiteral(n.initializer))
      result.push(n.initializer.text);
    ts.forEachChild(n, titles);
  };
  functions.forEach(titles);
} else {
  const original = parse(p.original);
  const old = [];
  const scan = n => { old.push(n); ts.forEachChild(n, scan); }; scan(original);
  const emit = n => printer.printNode(ts.EmitHint.Unspecified, n, n.getSourceFile());
  const names = ['Toolbar', 'ExportGroup', 'CountBreakdown'];
  const unchanged = names.map(name => emit(all.find(n =>
    ts.isFunctionDeclaration(n) && n.name?.text === name)) === emit(old.find(n =>
    ts.isFunctionDeclaration(n) && n.name?.text === name)));
  const predicate = list => emit(list.find(n => ts.isVariableDeclaration(n) &&
    n.name.getText() === 'awaitingOperator').initializer);
  const fleet = all.filter(n => ts.isJsxSelfClosingElement(n) &&
    n.tagName.getText(sf) === 'FleetTimingPanel').length;
  result = {unchanged, predicate: predicate(all) === predicate(old), fleet};
}
process.stdout.write(JSON.stringify(result));
"""


def _source_observation(mode: str, path: Path, **extra):
    return json.loads(_node(_AST, {
        "mode": mode, "source": path.read_text(), **extra,
    }))


def test_board_bands_receive_same_chrome_prop():
    # Arrange
    source = _SRC / "CardsBoard.tsx"
    # Act
    observed = _source_observation("bands", source)
    # Assert
    assert observed == {
        "props": ["internalChromeEnabled", "internalChromeEnabled"],
        "defaults": ["false"], "gate": True,
    }


def test_normal_tooltips_explain_views_without_provenance():
    # Arrange
    source = _SRC / "CardsBoard.tsx"
    # Act
    titles = _source_observation("titles", source)
    # Assert
    assert titles == [
        "Recent — review the newest tasks first",
        "Calendar — browse tasks by deadline or latest activity",
        "Time View — follow activity across the fleet",
        "Awaiting your decision. Open a task to review the request.",
    ]


def test_board_filters_and_fleet_timing_are_preserved():
    # Arrange
    original = subprocess.check_output(
        ["git", "-C", str(_ROOT), "show", f"{_BASE}:{_REL_BOARD}"],
        text=True, timeout=7, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    # Act
    observed = _source_observation(
        "preserved", _SRC / "CardsBoard.tsx", original=original,
    )
    # Assert
    assert observed == {"unchanged": [True, True, True], "predicate": True, "fleet": 1}
