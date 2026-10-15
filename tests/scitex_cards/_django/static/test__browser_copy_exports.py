"""Actual TS exports and native downloads; no connected Board acceptance claim."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_SRC = _ROOT / "src/scitex_cards/_django/frontend/src"
_BASE = "65d751e7f6cedf92abdd4e9e1c53aaa477c94e88"
_REL_SRC = "src/scitex_cards/_django/frontend/src"


def _dependency_root() -> Path:
    for root in (_ROOT, *_ROOT.parents):
        path = root / "src/scitex_cards/_django/frontend/node_modules"
        if (path / "typescript/lib/typescript.js").is_file():
            return path
    pytest.fail("Existing frontend TypeScript/esbuild dependency tree is required")


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


@pytest.fixture(scope="module")
def compiled_exports(tmp_path_factory):
    directory = tmp_path_factory.mktemp("actual-ts-exports")
    script = r"""
const fs = require('fs');
const ts = require(process.argv[1] + '/typescript');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const name of ['clipboard', 'exportBoard']) {
  const source = fs.readFileSync(p.source + '/' + name + '.ts', 'utf8');
  const result = ts.transpileModule(source, {fileName: name + '.ts',
    reportDiagnostics: true, compilerOptions: {target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.CommonJS}});
  if (result.diagnostics?.length)
    throw Error(ts.formatDiagnosticsWithColorAndContext(result.diagnostics, {
      getCurrentDirectory: () => p.source,
      getCanonicalFileName: f => f, getNewLine: () => '\n'}));
  fs.writeFileSync(p.output + '/' + name + '.js', result.outputText);
}
"""
    _node(script, {"source": str(_SRC), "output": str(directory)})
    return directory


def _invoke(directory, function, graph, ids, *, observe_mutation=False):
    script = r"""
const fs = require('fs');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const actual = require(p.directory + '/' +
  (p.function === 'formatTasksForCopy' ? 'clipboard' : 'exportBoard') + '.js');
const nodes = p.ids.map(id => p.graph.nodes.find(n => n.id === id)).filter(Boolean);
const before = JSON.stringify([p.graph, nodes, p.ids]);
const result = actual[p.function](p.graph,
  p.function === 'formatTasksForCopy' ? p.ids : nodes);
process.stdout.write(JSON.stringify(p.observe_mutation
  ? {result, unchanged: before === JSON.stringify([p.graph, nodes, p.ids])}
  : result));
"""
    return json.loads(_node(script, {
        "directory": str(directory), "function": function, "graph": graph,
        "ids": ids, "observe_mutation": observe_mutation,
    }))


@pytest.fixture
def graph():
    return {
        "store_path": "/srv/private/cards/tasks.json",
        "nodes": [
            {
                "id": "a", "title": "Alpha /srv/private/cards", "status": "deferred",
                "priority": 7, "repo": "alpha", "parent": None,
                "note": "  Authored /srv/private/note — 日本語  ",
                "store_path": "user-authored", "depends_on": ["stale"],
                "comments": [{
                    "ts": "2026-10-03", "author": "writer",
                    "text": "Keep /srv/private/comment Ω",
                }],
            },
            {
                "id": "b", "title": 'Beta, "quoted"\nsecond line', "status": "blocked",
                "priority": None, "repo": "beta", "parent": "a",
                "note": None, "comments": [],
            },
            {
                "id": "c", "title": "Finished", "status": "done", "priority": 0,
                "repo": "beta", "parent": None, "note": "uncategorized",
                "comments": [],
            },
            {
                "id": "d", "title": "Custom status", "status": "review", "priority": 2,
                "repo": None, "parent": None, "note": None, "comments": [],
            },
        ],
        "edges": [
            {"source": "b", "target": "a", "kind": "depends_on"},
            {"source": "a", "target": "c", "kind": "blocks"},
            {"source": "d", "target": "a", "kind": "unrelated"},
        ],
    }


def test_clipboard_omits_generated_location(compiled_exports, graph):
    # Arrange
    ids = ["a", "b"]
    # Act
    text = _invoke(compiled_exports, "formatTasksForCopy", graph, ids)
    generated_lines = [line for line in text.splitlines() if line.startswith("file: ")]
    # Assert
    assert generated_lines == []


def test_clipboard_preserves_selection_order_and_missing_ids(compiled_exports, graph):
    # Arrange
    ids = ["b", "missing", "a"]
    # Act
    text = _invoke(compiled_exports, "formatTasksForCopy", graph, ids)
    observation = (text.count("\n\n---\n\n"), [
        line for line in text.splitlines() if line.startswith("id: ")
    ])
    # Assert
    assert observation == (1, ["id: b", "id: a"])


def test_clipboard_preserves_edges_metadata_note_comments(compiled_exports, graph):
    # Arrange
    ids = ["a"]
    # Act
    text = _invoke(compiled_exports, "formatTasksForCopy", graph, ids)
    # Assert
    assert text == "\n".join([
        "# Alpha /srv/private/cards", "id: a", "status: deferred", "priority: 7",
        "repo: alpha", "parent: -", "depends_on: b", "blocks: c", "",
        "Authored /srv/private/note — 日本語", "", "comments:",
        "- 2026-10-03 writer: Keep /srv/private/comment Ω",
    ])


def test_uncategorized_note_is_not_added(compiled_exports, graph):
    # Arrange
    ids = ["c"]
    # Act
    text = _invoke(compiled_exports, "formatTasksForCopy", graph, ids)
    # Assert
    assert text == "\n".join([
        "# Finished", "id: c", "status: done", "priority: 0", "repo: beta",
        "parent: -", "depends_on: -", "blocks: -",
    ])


def test_markdown_omits_location_wrapper(compiled_exports, graph):
    # Arrange
    ids = ["a"]
    # Act
    text = _invoke(compiled_exports, "toMarkdown", graph, ids)
    generated_lines = [line for line in text.splitlines() if line.startswith("*store:")]
    # Assert
    assert generated_lines == []


def test_markdown_preserves_grouping_counts_and_edges(compiled_exports, graph):
    # Arrange
    ids = ["d", "c", "a", "b"]
    # Act
    text = _invoke(compiled_exports, "toMarkdown", graph, ids)
    # Assert
    assert text == "\n".join([
        "# scitex-cards — 4 tasks", "", "## blocked (1)", "",
        '- **Beta, "quoted"\nsecond line** `b` — repo:`beta` · parent:`a`', "",
        "## deferred (1)", "",
        "- **Alpha /srv/private/cards** `a` — p7 · repo:`alpha` · "
        "depends_on:[b] · blocks:[c] · 💬1",
        "", "## done (1)", "", "- **Finished** `c` — p0 · repo:`beta`", "",
        "## review (1)", "", "- **Custom status** `d` — p2", "",
    ])


def test_json_omits_generated_store_wrapper(compiled_exports, graph):
    # Arrange
    ids = ["a"]
    # Act
    payload = json.loads(_invoke(compiled_exports, "toJson", graph, ids))
    # Assert
    assert list(payload) == ["count", "tasks"]


def test_json_preserves_payload_and_reconstructed_edges(compiled_exports, graph):
    # Arrange
    ids = ["b", "a", "c"]
    expected_tasks = [
        {**graph["nodes"][1], "depends_on": [], "blocks": []},
        {**graph["nodes"][0], "depends_on": ["b"], "blocks": ["c"]},
        {**graph["nodes"][2], "depends_on": [], "blocks": []},
    ]
    # Act
    payload = json.loads(_invoke(compiled_exports, "toJson", graph, ids))
    # Assert
    assert payload == {"count": 3, "tasks": expected_tasks}


def test_csv_preserves_quotes_and_selection_order(compiled_exports, graph):
    # Arrange
    ids = ["b", "a", "c"]
    # Act
    text = _invoke(compiled_exports, "toCsv", graph, ids)
    rows = list(csv.reader(io.StringIO(text)))
    # Assert
    assert rows == [
        ["id", "title", "status", "priority", "repo", "parent", "depends_on",
         "blocks", "comments"],
        ["b", 'Beta, "quoted"\nsecond line', "blocked", "", "beta", "a", "", "", "0"],
        ["a", "Alpha /srv/private/cards", "deferred", "7", "alpha", "", "b", "c", "1"],
        ["c", "Finished", "done", "0", "beta", "", "", "", "0"],
    ]


@pytest.mark.parametrize("function", [
    "formatTasksForCopy", "toMarkdown", "toJson", "toCsv",
])
def test_formatters_do_not_mutate_inputs(compiled_exports, graph, function):
    # Arrange
    ids = ["b", "missing", "a"]
    # Act
    observation = _invoke(
        compiled_exports, function, graph, ids, observe_mutation=True,
    )
    # Assert
    assert observation["unchanged"] is True


def test_clipboard_and_download_actions_are_preserved():
    # Arrange
    names = [("clipboard.ts", "copyTasks"), ("exportBoard.ts", "downloadText")]
    sources = []
    for filename, function in names:
        original = subprocess.check_output(
            ["git", "-C", str(_ROOT), "show", f"{_BASE}:{_REL_SRC}/{filename}"],
            text=True, timeout=7, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        sources.append({
            "source": (_SRC / filename).read_text(), "original": original,
            "function": function,
        })
    script = r"""
const fs = require('fs');
const ts = require(process.argv[1] + '/typescript');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const printer = ts.createPrinter({removeComments: true});
const declaration = (text, name) => {
  const source = ts.createSourceFile('actual.ts', text,
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const node = source.statements.find(n => ts.isFunctionDeclaration(n) &&
    n.name?.text === name);
  if (!node) throw Error('Actual action is missing: ' + name);
  return printer.printNode(ts.EmitHint.Unspecified, node, source);
};
process.stdout.write(JSON.stringify(p.sources.map(s =>
  declaration(s.source, s.function) === declaration(s.original, s.function))));
"""
    # Act
    observed = json.loads(_node(script, {"sources": sources}))
    # Assert
    assert observed == [True, True]
