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


_LOAD_ERROR_AST = r"""
const fs = require('fs');
const ts = require(process.argv[1] + '/typescript');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const parse = text => ts.createSourceFile('CardsBoard.tsx', text,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const sf = parse(p.source);
const visit = root => {
  const nodes = [];
  const walk = n => { nodes.push(n); ts.forEachChild(n, walk); };
  walk(root); return nodes;
};
const board = file => file.statements.find(n =>
  ts.isFunctionDeclaration(n) && n.name?.text === 'CardsBoard');
const isErrorBranch = n => ts.isIfStatement(n) &&
  ts.isBinaryExpression(n.expression) &&
  n.expression.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken &&
  ts.isIdentifier(n.expression.left) && n.expression.left.text === 'error' &&
  ts.isPrefixUnaryExpression(n.expression.right) &&
  n.expression.right.operator === ts.SyntaxKind.ExclamationToken &&
  ts.isIdentifier(n.expression.right.operand) &&
  n.expression.right.operand.text === 'graph';
const current = board(sf);
const branch = current.body.statements.find(isErrorBranch);
if (!branch) throw Error('Actual error/no-graph branch is required');
const nodes = visit(branch);
const attribute = (element, name) => element.openingElement.attributes.properties
  .find(n => ts.isJsxAttribute(n) && n.name.text === name);
const className = element => attribute(element, 'className')?.initializer?.text;
const elements = nodes.filter(ts.isJsxElement);
const detail = elements.find(n => className(n) === 'stx-cards-status__detail');
if (!detail) throw Error('Actual diagnostic detail is required');
const expression = detail.parent;
const gate = ts.isParenthesizedExpression(expression) ? expression.parent : expression;
const owner = gate.parent;
let result;
if (p.mode === 'gate') {
  const defaults = current.parameters[0].name.elements.filter(n =>
    n.name.text === 'internalChromeEnabled').map(n => n.initializer?.kind);
  const value = detail.children.filter(ts.isJsxExpression)
    .map(n => ts.isIdentifier(n.expression) ? n.expression.text : null);
  result = {
    defaults: defaults.map(n => ts.SyntaxKind[n]),
    gate: ts.isBinaryExpression(gate) &&
      gate.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken &&
      ts.isIdentifier(gate.left) ? gate.left.text : null,
    consumed: ts.isJsxExpression(owner) && owner.expression === gate,
    values: value,
    rawHtmlAttributes: nodes.filter(n => ts.isJsxAttribute(n) &&
      n.name.text === 'dangerouslySetInnerHTML').length,
    parseErrors: sf.parseDiagnostics.length,
  };
} else if (p.mode === 'workflow') {
  const text = element => element.children.filter(ts.isJsxText)
    .map(n => n.text.trim()).filter(Boolean).join(' ');
  const head = elements.find(n => className(n) === 'stx-cards-status__head');
  const retry = elements.find(n => className(n) === 'stx-cards-status__retry');
  const click = attribute(retry, 'onClick')?.initializer?.expression;
  const call = click?.body;
  result = {
    lead: text(head), retry: text(retry),
    type: attribute(retry, 'type')?.initializer?.text,
    retryCall: ts.isArrowFunction(click) && click.parameters.length === 0 &&
      ts.isVoidExpression(call) && ts.isCallExpression(call.expression) &&
      call.expression.arguments.length === 0 ? call.expression.expression.text : null,
    identity: nodes.filter(n => ts.isJsxExpression(n) &&
      ts.isIdentifier(n.expression) && n.expression.text === 'band').length,
    role: elements.map(n => attribute(n, 'role')?.initializer?.text)
      .filter(n => n !== undefined),
  };
} else {
  const oldFile = parse(p.original);
  const original = board(oldFile);
  const printer = ts.createPrinter({removeComments: true});
  const emit = n => printer.printNode(ts.EmitHint.Unspecified, n, n.getSourceFile());
  const remaining = functionNode => functionNode.body.statements
    .filter(n => !isErrorBranch(n)).map(emit);
  result = {
    parameters: current.parameters.map(emit).join('') ===
      original.parameters.map(emit).join(''),
    remainingBoard: JSON.stringify(remaining(current)) ===
      JSON.stringify(remaining(original)),
    otherDeclarations: JSON.stringify(sf.statements.filter(n => n !== current).map(emit)) ===
      JSON.stringify(oldFile.statements.filter(n => n !== original).map(emit)),
  };
}
process.stdout.write(JSON.stringify(result));
"""


def test_load_error_detail_consumes_default_false_chrome_gate():
    # Arrange
    payload = {"mode": "gate", "source": (_SRC / "CardsBoard.tsx").read_text()}
    # Act
    observed = json.loads(_node(_LOAD_ERROR_AST, payload))
    # Assert
    assert observed == {
        "defaults": ["FalseKeyword"], "gate": "internalChromeEnabled",
        "consumed": True, "values": ["error"], "rawHtmlAttributes": 0,
        "parseErrors": 0,
    }


def test_load_error_keeps_identity_failure_lead_and_real_retry():
    # Arrange
    payload = {"mode": "workflow", "source": (_SRC / "CardsBoard.tsx").read_text()}
    # Act
    observed = json.loads(_node(_LOAD_ERROR_AST, payload))
    # Assert
    assert observed == {
        "lead": "The board could not load.", "retry": "Retry", "type": "button",
        "retryCall": "loadBoard", "identity": 1, "role": ["alert"],
    }


def test_load_error_gate_preserves_other_board_states_and_declarations():
    # Arrange
    original = subprocess.check_output(
        ["git", "-C", str(_ROOT), "show", f"1a3815aac3b4162cf3adea29344deea280d3aab8:{_REL_BOARD}"],
        text=True, timeout=7, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    payload = {
        "mode": "preserved", "source": (_SRC / "CardsBoard.tsx").read_text(),
        "original": original,
    }
    # Act
    observed = json.loads(_node(_LOAD_ERROR_AST, payload))
    # Assert
    assert observed == {
        "parameters": True, "remainingBoard": True, "otherDeclarations": True,
    }
