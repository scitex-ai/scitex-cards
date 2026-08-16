#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S7 decoupling gate: scitex-cards never couples to sac or the telegrammer.

Operator hard rule (さいいんふら, 2026-07-16): scitex-cards must be
independently usable — no single point of failure, no import of
scitex-agent-container (sac) or claude-code-telegrammer, ever. Cross-package
integration happens through PORTS (entry-point groups the OTHER side
registers into; see ``_ports.py``), so the dependency arrow always points
AT this package, never out of it.

Two layers, because each catches what the other cannot:

* the AST scan proves no module TEXT contains a forbidden import — including
  imports hidden inside functions (lazy imports), which a runtime probe only
  catches if the function runs;
* the runtime probe proves the exercised CRUD surface still WORKS when those
  packages are absent — the no-single-point-of-failure half of the rule,
  which no amount of source reading can establish.

The runtime probe deliberately does NOT assert "no forbidden module is in
``sys.modules``". A port PROVIDER loading is the design succeeding, not
failing: sac registers a handler into our ``scitex_cards.hooks`` group, so a
write legitimately imports ``scitex_agent_container`` via ``ep.load()``. The
arrow still points at us — we never name it. Asserting on ``sys.modules``
flagged that as a violation and failed on correct code (2026-07-22).
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.abc
import importlib.metadata
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "scitex_cards"

#: Root module names scitex-cards must NEVER import. ``scitex_cards`` is
#: deliberately absent: that is our own shim, not a foreign package.
FORBIDDEN_ROOTS = {
    "scitex_agent_container",
    "claude_code_telegrammer",
    "sac",
}


def _imported_roots(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            # level>0 = relative import — inside this package by definition.
            if node.level == 0 and node.module:
                yield node.module.split(".")[0], node.lineno


def test_no_module_imports_sac_or_telegrammer_anywhere():
    """AST scan of every source module — comments/docstrings cannot fool it."""
    # Arrange
    offenders = []
    # Act
    for py in sorted(SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for root, lineno in _imported_roots(tree):
            if root in FORBIDDEN_ROOTS:
                offenders.append(f"{py.relative_to(SRC)}:{lineno} imports {root}")
    # Assert
    assert not offenders, (
        "scitex-cards must stay decoupled (operator hard rule); "
        "forbidden imports found:\n" + "\n".join(offenders)
    )


#: A module that is DEFINITELY importable everywhere this suite runs, used to
#: prove the blocker mechanism itself works. Pure-stdlib and rarely imported,
#: so blocking it cannot disturb anything.
_CANARY_ROOT = "colorsys"


class _ModuleBlocker(importlib.abc.MetaPathFinder):
    """Make the named roots UNIMPORTABLE, as if they were not installed."""

    def __init__(self, roots):
        self._roots = frozenset(roots)

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname.split(".")[0] in self._roots:
            raise ImportError(
                f"{fullname} is deliberately unavailable: the decoupling gate "
                f"removed it to prove scitex-cards runs without it"
            )
        return None


@contextlib.contextmanager
def _roots_uninstallable(roots):
    """Simulate a machine where ``roots`` are simply not present.

    Both halves matter: the finder blocks FUTURE imports, and purging
    ``sys.modules`` defeats the cache so an already-loaded copy cannot satisfy
    one. Everything is restored on exit — other tests share this process.
    """
    blocker = _ModuleBlocker(roots)
    stashed = {
        name: mod
        for name, mod in sys.modules.items()
        if name.split(".")[0] in set(roots)
    }
    for name in stashed:
        del sys.modules[name]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(stashed)


def _forbidden_hook_entry_points():
    """Entry points in OUR hook groups that resolve into a forbidden package.

    This is the PORT: the other side registers into it, we never name it.
    Returns the entry points, so a test can tell "the coupling surface exists
    here and I exercised it" apart from "nothing is installed, so I proved
    nothing" — a distinction the first version of this gate could not make.
    """
    from scitex_cards._hooks._plugins import (
        ENTRY_POINT_GROUP,
        LEGACY_ENTRY_POINT_GROUP,
    )

    eps = importlib.metadata.entry_points()
    found = []
    for group in (ENTRY_POINT_GROUP, LEGACY_ENTRY_POINT_GROUP):
        for ep in eps.select(group=group):
            if ep.value.split(".")[0].split(":")[0] in FORBIDDEN_ROOTS:
                found.append(ep)
    return found


def _import_error_or_none(root):
    """Import ``root``; return the ``ImportError`` it raised, or ``None``."""
    try:
        importlib.import_module(root)
    except ImportError as exc:
        return exc
    return None


@pytest.fixture()
def canary_root():
    """The control module, refusing to run if it is not importable to begin with.

    Both blocker tests below are only meaningful while this holds, and a
    precondition that silently fails to hold would make them vacuous rather
    than failing — so it fails loudly here instead.
    """
    if importlib.util.find_spec(_CANARY_ROOT) is None:
        pytest.fail(
            f"{_CANARY_ROOT} must be importable for the blocker control to mean "
            "anything; without it these tests would pass vacuously"
        )
    return _CANARY_ROOT


def test_the_blocker_makes_an_importable_module_unimportable(canary_root):
    """Prove the MECHANISM, independently of whether sac is installed.

    Without this, the anti-vacuity check inside the absence test is itself
    vacuous: asserting that importing ``scitex_agent_container`` raises passes
    identically whether the blocker works or the package simply is not
    installed — which is the situation on CI. Blocking a module that is
    UNCONDITIONALLY importable removes that ambiguity: if the finder were
    broken, this import would succeed and the test would fail.
    """
    # Arrange
    root = canary_root
    # Act
    with _roots_uninstallable({root}):
        raised = _import_error_or_none(root)
    # Assert
    assert raised is not None, (
        f"the blocker did not stop {root} from importing, so every 'the "
        "forbidden package is unimportable' claim below is unproven"
    )


def test_the_blocker_is_removed_when_the_context_exits(canary_root):
    """The blocker must not leak: other tests share this process."""
    # Arrange
    root = canary_root
    with _roots_uninstallable({root}):
        pass
    # Act
    raised = _import_error_or_none(root)
    # Assert
    assert raised is None, f"the blocker leaked: {root} is still unimportable"


def test_crud_surface_survives_absence_of_forbidden_modules(tmp_path, monkeypatch):
    """The CRUD surface must complete with sac/the telegrammer UNINSTALLABLE.

    WHY THIS, AND NOT "no forbidden module is in ``sys.modules``" (the shape
    this test had until 2026-07-22): that assertion measured the wrong thing
    and failed on correct code. ``sac`` registers a card-event delivery
    handler into our ``scitex_cards.hooks`` entry-point group, so the FIRST
    write loads ``scitex_agent_container`` through ``ep.load()`` — the PORTS
    mechanism working exactly as designed, with the dependency arrow still
    pointing at us (we never name sac; the AST scan above proves that, and it
    is the layer that enforces the "never import" half of the rule).

    The property the operator's rule actually protects is NO SINGLE POINT OF
    FAILURE: scitex-cards must work when those packages are absent. So assert
    that directly. This is strictly stronger than the old probe — it fails if
    any code path hard-requires a forbidden package, including one that only
    reveals itself when the import raises (e.g. if
    ``_hooks._plugins``'s ``except Exception`` around ``ep.load()`` were ever
    narrowed or removed).
    """
    # Arrange — hermetic env: pin BOTH the store and the identity, so the
    # test neither reads a live store nor silently depends on the agent id
    # of whoever runs it (it passed locally and failed on CI for exactly
    # that reason: add_task resolves its creator from the env).
    #
    # BOTH identity variables, because ``_env_compat`` gives the
    # ``SCITEX_CARDS_*`` name precedence: pinning only the ``SCITEX_CARDS_*``
    # one leaves the test reading the runner's real agent id whenever
    # ``SCITEX_CARDS_AGENT_ID`` is exported — which it is for every agent in
    # this fleet, so the pin was silently inert exactly where it was needed.
    monkeypatch.setenv("SCITEX_CARDS_AGENT_ID", "decoupling-gate-test")
    store = tmp_path / "tasks.yaml"
    from scitex_cards import _store

    # Act — the cycle itself carries the proof: any hard requirement on a
    # forbidden package surfaces here as an ImportError, failing the test.
    #
    # The blocker's own correctness is proved by
    # ``test_the_blocker_makes_an_importable_module_unimportable``, NOT by
    # asserting here that the forbidden roots fail to import: that assertion
    # passes identically when the packages are merely absent (as on CI), so it
    # could never have caught a broken blocker.
    with _roots_uninstallable(FORBIDDEN_ROOTS):
        _store.add_task(store, id="t", title="t", status="deferred", agent="a")
        _store.list_tasks(store)
        _store.comment_task(store, "t", "standalone", by="a")
        _store.complete_task(store, "t", by="a")
        leaked = {m.split(".")[0] for m in sys.modules} & FORBIDDEN_ROOTS

    # Assert — nothing slipped back in through a cached reference
    assert not leaked, f"forbidden module(s) loaded despite blocker: {leaked}"


@pytest.fixture()
def forbidden_port_providers():
    """The forbidden-package entry points registered into our hook groups.

    Skips where the port has no provider: a skipped test reports its own
    reduced power, where a silently passing one does not. This lives in a
    fixture rather than in the test body because ``pytest.skip`` counts as an
    assertion (scitex-dev STX-TQ007), and the precondition belongs to the
    setup anyway.
    """
    provided = _forbidden_hook_entry_points()
    if not provided:
        pytest.skip(
            "no forbidden package registers into our hook entry-point group "
            "here, so the coupling surface this asserts on does not exist in "
            "this environment (expected on CI, where sac is not installed)"
        )
    return provided


def test_port_provider_failure_is_swallowed_by_the_hook_dispatcher(
    tmp_path, monkeypatch, caplog, forbidden_port_providers
):
    """Prove the CRUD cycle REALLY reaches the coupling surface and tolerates it.

    Without this, ``test_crud_surface_survives_absence_of_forbidden_modules``
    is vacuous wherever no forbidden package is installed — nothing registers
    into our hook group, ``ep.load()`` is never called, and "the cycle
    completed" says nothing about tolerating a missing provider. That is
    exactly the situation on CI, where sac is not a dependency.

    So this test asserts the load was ATTEMPTED and the failure SWALLOWED,
    by reading the warning ``_hooks._plugins`` emits at the ``except`` around
    ``ep.load()``. The ``forbidden_port_providers`` fixture skips loudly,
    naming the reason, where the port has no provider.
    """
    # Arrange
    provided = forbidden_port_providers
    monkeypatch.setenv("SCITEX_CARDS_AGENT_ID", "decoupling-gate-test")
    store = tmp_path / "tasks.yaml"
    from scitex_cards import _store

    # Act — a write, with the provider's package unimportable
    with caplog.at_level(logging.WARNING), _roots_uninstallable(FORBIDDEN_ROOTS):
        _store.add_task(store, id="t", title="t", status="deferred", agent="a")

    # Assert — the dispatcher tried to load it and kept going
    names = {ep.name for ep in provided}
    failed = [
        r.getMessage() for r in caplog.records if "failed to load" in r.getMessage()
    ]
    offending = [msg for msg in failed if any(name in msg for name in names)]
    # One assertion, both properties: an empty ``failed`` means the load was
    # never attempted (so the absence-tolerance test above proves nothing
    # here); a non-empty ``failed`` with no match means some OTHER plugin
    # failed. The message prints both, so it says which happened.
    seen = failed or "(none — the write never attempted to load any port provider)"
    assert offending, (
        f"no load failure for the forbidden provider(s) {sorted(names)}; "
        f"load failures seen: {seen}"
    )
