"""Default-board presentation and actual inline JS protocol controls.

Node executes production functions and the real searchQuery.js module against
explicit browser API collaborators. These are not native DOM or connected
Store tests. Native-browser execution has a separate owned recipe/prerequisite;
this mirror does not add a mandatory Playwright dependency to public CI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.test import override_settings

from tests.scitex_cards._django.test__standalone_chrome_eligibility import (
    _render,
    _request,
)

ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = ROOT / "src/scitex_cards/_django/templates/scitex_cards/board_v3.html"
SEARCH = ROOT / "src/scitex_cards/_django/static/scitex_cards/board_v3/searchQuery.js"


class _Options(HTMLParser):
    def __init__(self):
        super().__init__()
        self.select = None
        self.option = None
        self.options = {}
        self.attributes = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.attributes[attributes["id"]] = attributes
        if tag == "select":
            self.select = attributes.get("id")
            self.options[self.select] = []
        if tag == "option" and self.select is not None:
            self.option = [attributes.get("value", ""), ""]

    def handle_data(self, text):
        if self.option is not None:
            self.option[1] += text

    def handle_endtag(self, tag):
        if tag == "option" and self.option is not None:
            self.options[self.select].append(tuple(self.option))
            self.option = None
        if tag == "select":
            self.select = None


def _options(html):
    parser = _Options()
    parser.feed(html)
    return parser


def _function(name):
    source = TEMPLATE.read_text()
    start = re.search(r"(?:async )?function " + re.escape(name) + r"\(", source)
    if start is None:
        raise LookupError("production inline function missing: " + name)
    # These production declarations close at four-space indentation. Keep
    # their exact body instead of translating an algorithm into Python.
    end = source.index("\n    }", start.start()) + len("\n    }")
    return source[start.start():end]


def _run(operation, payload):
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node is a required explicit JS-control prerequisite")
    functions = "\n".join(_function(n) for n in
                          ("hiddenSet", "fuzzyMatch", "passes", "populateFilters",
                           "copyCardText", "toast"))
    script = "const payload = " + json.dumps(payload) + ";\n" + """
const writes = [], copied = [], elements = new Map();
function element() {
  const classes = new Set();
  return {value: '', innerHTML: '', children: [], style: {},
    appendChild(child) { this.children.push(child); },
    classList: {add(x) { classes.add(x); }, remove(x) { classes.delete(x); },
      toggle(x, on) { if (on) classes.add(x); else classes.delete(x); },
      contains(x) { return classes.has(x); }}};
}
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  },
  createElement() { const e = element(); e.select = () => copied.push(e.value); return e; },
  body: {appendChild() {}, removeChild() {}}, execCommand() { return true; }
};
for (const [id, value] of Object.entries(payload.selected || {}))
  document.getElementById(id).value = value;
const STATE = {graph: {store_path: '/private/generated/store',
  nodes: payload.nodes || []}, filters: {project: '', host: '', status: '',
  blocker: '', agent: '', blockingMe: false, showDone: true, showHidden: false,
  search: '', date: '', ...(payload.filters || {})}};
const localStorage = {getItem() { return JSON.stringify(payload.hidden || []); }};
const window = {isSecureContext: payload.clipboard !== 'legacy',
  STX: {searchQuery: require(SEARCH_MODULE)}};
const navigator = {clipboard: {async writeText(text) {
  writes.push(text);
  if (payload.clipboard === 'refuse') throw new Error('clipboard permission denied');
}}};
""".replace("SEARCH_MODULE", json.dumps(str(SEARCH))) + functions + "\n"
    script += """
(async () => {
  let result;
  if (OPERATION === 'filters') {
    populateFilters();
    result = Object.fromEntries([...elements].map(([id, e]) =>
      [id, {html: e.innerHTML, value: e.value}]));
  } else if (OPERATION === 'passes') {
    result = STATE.graph.nodes.map(passes);
  } else if (OPERATION === 'copy') {
    await copyCardText(payload.id);
    const el = document.getElementById('toast');
    result = {writes, copied, message: el.children.map(c => c.textContent).join(''),
      error: el.classList.contains('toast--err')};
  } else throw new Error('unknown test operation');
  console.log(JSON.stringify(result));
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("OPERATION", json.dumps(operation))
    completed = subprocess.run([node, "--input-type=commonjs", "-e", script],
                               check=True, capture_output=True, text=True, timeout=7)
    return json.loads(completed.stdout)


def _populated_select(result, identifier):
    return _options('<select id="' + identifier + '">' +
                    result[identifier]["html"] + '</select>').options[identifier]


@pytest.mark.parametrize("decision", [False, True], ids=["quiet", "diagnostic"])
def test_default_board_editor_keeps_blocker_values_with_readable_labels(decision):
    # Arrange
    request = _request("/board/")
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=decision):
        options = _options(_render(request, "board_v3.html", view_path="board/"))
    # Assert
    assert options.options["d-edit-blocker"] == [
        ("", "(none)"), ("compute", "Compute"), ("quota", "Resource quota"),
        ("user-pending", "Awaiting your response"),
        ("task-dependency", "Task dependency"),
        ("operator-decision", "Awaiting your decision")]


@pytest.mark.parametrize("decision", [False, True], ids=["quiet", "diagnostic"])
def test_default_board_blocker_help_describes_the_user_action(decision):
    # Arrange
    request = _request("/board/")
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=decision):
        options = _options(_render(request, "board_v3.html", view_path="board/"))
    # Assert
    assert options.attributes["f-blocker"]["title"] == "Show what each card is waiting for"


@pytest.mark.parametrize("decision", [False, True], ids=["quiet", "diagnostic"])
def test_default_board_awaiting_queue_uses_the_readable_label(decision):
    # Arrange
    request = _request("/board/")
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=decision):
        html = _render(request, "board_v3.html", view_path="board/")
    # Assert
    assert "<span>🚧 awaiting your decision</span>" in html


def test_populated_blocker_filter_preserves_values_and_friendly_labels():
    # Arrange
    payload = {"nodes": [{"id": "c1", "project": "paper"}]}
    # Act
    options = _populated_select(_run("filters", payload), "f-blocker")
    # Assert
    assert options == [("", "All blockers"), ("compute", "Compute"),
                       ("dependency", "Task dependency"), ("dep", "Task dependency"),
                       ("operator-decision", "Awaiting your decision"),
                       ("agent-wait", "Waiting for an agent"), ("none", "None"),
                       ("__none", "No blocker")]


def test_populated_status_filter_keeps_the_closed_machine_values():
    # Arrange
    payload = {"nodes": [{"id": "c1"}]}
    # Act
    options = _populated_select(_run("filters", payload), "f-status")
    # Assert
    assert [value for value, label in options] == [
        "", "goal", "done", "in_progress", "blocked", "deferred", "failed", "cancelled"]


def test_refresh_preserves_an_existing_project_choice():
    # Arrange
    payload = {"nodes": [{"id": "c1", "project": "paper"}],
               "selected": {"f-project": "paper"}}
    # Act
    result = _run("filters", payload)
    # Assert
    assert result["f-project"]["value"] == "paper"


@pytest.mark.parametrize("filters,task,expected", [
    pytest.param({"status": "goal"}, {"status": "goal"}, True, id="goal-status-match"),
    pytest.param({"status": "goal"}, {"status": "blocked"}, False, id="raw-status-mismatch"),
    pytest.param({"showDone": False}, {"status": "done"}, False, id="done-hidden"),
    pytest.param({"blocker": "dependency"}, {"blocker": "dependency"}, True, id="dependency-match"),
    pytest.param({"blocker": "dependency"}, {"blocker": "dep"}, False,
                 id="blocker-alias-not-collapsed"),
    pytest.param({"blocker": "__none"}, {}, True, id="no-blocker-match"),
    pytest.param({"blocker": "__none"}, {"blocker": "none"}, False, id="none-token-is-present"),
    pytest.param({"blockingMe": True},
                 {"status": "blocked", "blocker": "operator-decision"}, True,
                 id="awaiting-decision"),
    pytest.param({"blockingMe": True},
                 {"status": "in_progress", "blocker": "operator-decision"}, False,
                 id="decision-requires-blocked"),
    pytest.param({"blockingMe": True}, {"status": "blocked", "blocker": "compute"}, False,
                 id="decision-requires-exact-blocker"),
    pytest.param({"search": "kind:compute"}, {"kind": "compute"}, True, id="kind-qualifier-match"),
    pytest.param({"search": "kind:compute"}, {"kind": "goal"}, False, id="kind-qualifier-mismatch"),
])
def test_existing_status_blocker_kind_and_awaiting_predicates(filters, task, expected):
    # Arrange
    payload = {"filters": filters, "nodes": [{"id": "c1", **task}]}
    # Act
    result = _run("passes", payload)
    # Assert
    assert result == [expected]


@pytest.mark.parametrize("clipboard", ["modern", "legacy"], ids=["clipboard-api", "textarea"])
def test_copy_retains_user_authored_path_text_and_workflow_fields(clipboard):
    # Arrange
    payload = {"id": "c1", "clipboard": clipboard,
               "nodes": [{"id": "c1", "title": "Read /my/project/result",
                          "project": "paper", "status": "blocked",
                          "blocker": "operator-decision"}]}
    # Act
    observed = _run("copy", payload)
    # Assert
    assert observed["writes"] + observed["copied"] == [
        "Read /my/project/result · id:c1 · project:paper · status:blocked"
        " · blocker:operator-decision"]


def test_copy_keeps_task_text_fallback_and_unknown_status():
    # Arrange
    payload = {"id": "c1", "nodes": [{"id": "c1", "task": "Review the report"}]}
    # Act
    observed = _run("copy", payload)
    # Assert
    assert observed["writes"] == ["Review the report · id:c1 · status:?"]


def test_copy_unknown_card_keeps_the_visible_refusal():
    # Arrange
    payload = {"id": "missing", "nodes": [{"id": "c1", "title": "Present"}]}
    # Act
    observed = _run("copy", payload)
    # Assert
    assert observed == {"writes": [], "copied": [], "message": "card not found", "error": True}


def test_copy_permission_failure_keeps_the_visible_error():
    # Arrange
    payload = {"id": "c1", "clipboard": "refuse", "nodes": [{"id": "c1", "title": "Review"}]}
    # Act
    observed = _run("copy", payload)
    # Assert
    assert observed == {"writes": ["Review · id:c1 · status:?"], "copied": [],
                        "message": "✗ copy failed: clipboard permission denied", "error": True}
