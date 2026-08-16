#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify a package's REAL version BY CONTENT — never by the version string alone.

WHY THIS EXISTS (incident 2026-07-12)
-------------------------------------
``importlib.metadata.version(dist)`` reads the ``.dist-info`` directory. That
directory can OUTLIVE the code it describes, and then it lies — confidently,
permanently, and with nothing anywhere reporting a problem:

* scitex-cards's own container: an old ``pip install -e`` left an ORPHANED
  ``scitex_cards-0.7.26.dist-info`` in site-packages with NO package files beside
  it, plus a path entry pointing at the live repo. The CODE loaded fresh from the
  working tree (0.8.7); the VERSION reported 0.7.26. Thirty releases apart.
* sac's container, independently: the BAKED dist-info reports a fossil while the
  code is bind-mounted from host source and current.
* 2026-07-10: a subagent's editable install DELETED ``site-packages/scitex_cards``
  and repointed the venv at its worktree. The dist-info stayed — so every version
  check reported a healthy install *with no code behind it*.

The fleet's drift detector (``scitex-dev ecosystem check-versions``, named in the
constitution) reads that exact string. **A drift detector reading a fossilised
version is a drift detector turned off**: it cries "stale, needs deploy" forever
on a container that is current, or blesses a genuinely stale one whose metadata
happens to look right. It cannot distinguish the two.

This module is the fix: it reports what the CODE says, what the METADATA says,
and whether they agree — so a caller can never be fooled by the string alone.

THE THIRD FAILURE MODE — DISK TRUTH IS NOT PROCESS TRUTH
--------------------------------------------------------
**This probe reports what is on DISK. A long-lived process may be running
something else entirely, and no version number will ever tell you.**

Found by scitex-dev (2026-07-12) while checking a claim of mine — which is the
only reason it is written down here. Their symptom: an ``update_task`` call failed
for HOURS with an old-enum validator message. A mid-session ``pip install
--upgrade`` changed the code on disk, and the failures **continued,
byte-identical**. Only a full process restart cleared them.

The cause is neither pip nor the metadata. Python imports a module ONCE, into
``sys.modules``; upgrading the files on disk does not touch the module objects a
running process already holds. So a server can serve stale code from memory while
its disk, its ``.dist-info``, and this probe ALL agree the install is current —
and every one of them is telling the truth. They are simply answering a different
question than the one that was asked.

You cannot detect this from a version string. Not the metadata's, and not the
source's: both describe the DISK.

**The only reliable detector is to probe the LOADED MODULE for a symbol** — which
is exactly what ``features`` does, because ``hasattr`` reads ``sys.modules`` and
therefore interrogates the code the process is ACTUALLY RUNNING::

    p = probe_install("scitex-cards", features={
        "post_migration_enum": "scitex_cards._model:VALID_BLOCKERS",
    })
    if not p.features["post_migration_enum"]:
        # THIS PROCESS is running pre-migration code, whatever the disk says.
        # An upgrade will NOT fix it — only a RESTART will.

The rule: **to know what a process is running, ask the process — not the package
manager.** And when the answer is "stale", the remedy is a RESTART; an upgrade
will not touch it.

(A tempting shortcut — comparing the module file's mtime against the process
start time — was tried and DELIBERATELY NOT SHIPPED: it depends on boot-time
arithmetic and clock skew, and it gave a wrong answer on the first live box it
was pointed at. Shipping a flaky detector for a false-confidence bug would be
self-parody. Symbol probing is exact; use it.)

DESIGN
------
Pure, dependency-light, and **it never raises**: a probe that can crash is a
probe that gets wrapped in ``try/except`` and ignored. Every failure mode comes
back as a populated result with an actionable ``hint``, because a diagnostic that
fails silently is the very disease it is meant to detect.

The probe is generic — pass any distribution name. sac probes ``sac``, scitex-dev
probes the ecosystem; nothing here is scitex-cards-specific.
"""

from __future__ import annotations

import importlib
import importlib.metadata as _md
from dataclasses import dataclass, field
from pathlib import Path

#: Install shapes the probe can distinguish.
#:
#: ``wheel``    — code lives under site-packages, installed from a built dist.
#:                Metadata is TRUSTWORTHY: it shipped with the code it describes.
#: ``editable`` — code lives in a source tree OUTSIDE site-packages (an editable
#:                install, a bind-mount, or a bare ``sys.path`` entry). Metadata is
#:                a SNAPSHOT taken at install time and drifts freely from the code.
#: ``orphaned`` — metadata exists but the module CANNOT BE IMPORTED. The worst
#:                case: every version check "passes" against code that is not there.
#: ``unmanaged``— the module imports but has NO metadata at all (a bare path entry).
#:                Honest by omission: nothing is claimed, so nothing can lie.
#: ``absent``   — no metadata AND no importable code. Not installed. NOT a lie —
#:                and kept distinct from ``orphaned`` on purpose: reporting an
#:                absent package as "orphaned .dist-info" would send the reader
#:                hunting for a directory that does not exist. A diagnostic that
#:                gives a confidently wrong hint IS the disease this module treats.
KIND_WHEEL = "wheel"
KIND_EDITABLE = "editable"
KIND_ORPHANED = "orphaned"
KIND_UNMANAGED = "unmanaged"
KIND_ABSENT = "absent"

#: Directory names that mark an installed-package root.
_SITE_MARKERS = ("site-packages", "dist-packages")

#: How far up from the module to look for the project file that carries the
#: SOURCE-OF-TRUTH version. 5 covers ``<root>/src/<pkg>/__init__.py`` with room
#: to spare; an unbounded walk would climb out of the project on a stray layout.
_PYPROJECT_SEARCH_DEPTH = 5


@dataclass
class InstallProbe:
    """What the metadata claims, what the code says, and whether they agree."""

    dist: str
    kind: str
    metadata_version: str | None = None
    code_version: str | None = None
    module_path: str | None = None
    source_root: str | None = None
    #: True only when the probe can POSITIVELY confirm metadata matches the code.
    #: An un-verifiable install is never reported as honest — "I could not check"
    #: must never be rendered as "it is fine".
    honest: bool = False
    detail: str = ""
    hint: str | None = None
    #: Populated when the probe itself hit an error, so a caller can tell
    #: "the install is broken" apart from "the probe is broken".
    probe_error: str | None = None
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def trustworthy(self) -> bool:
        """True when ``metadata_version`` may be used as the version ON DISK.

        The ONE question a deploy check should ask of the filesystem. False for an
        orphaned or drifted install — i.e. exactly when the version string would
        mislead.

        .. warning::

           ``trustworthy`` is a statement about the DISK, never about a running
           PROCESS. A long-lived server can serve stale code from ``sys.modules``
           while every disk-level signal here reports a perfectly current install
           — and none of them is lying; they are answering a different question.
           See "THE THIRD FAILURE MODE" in the module docstring. To ask what a
           PROCESS is running, use ``features`` (symbol probing), and remember
           that the remedy for a stale process is a RESTART, not an upgrade.
        """
        return self.honest and self.kind in (KIND_WHEEL, KIND_EDITABLE)


def _read_pyproject_version(root: Path) -> str | None:
    """The version literal from ``root/pyproject.toml`` — the source's own claim.

    This is the SSoT for an editable install: the code came from this tree, so
    the tree's declared version is what the code IS, whatever a stale dist-info
    left behind says.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py<3.11
        return None
    path = root / "pyproject.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    project = data.get("project")
    if isinstance(project, dict):
        v = project.get("version")
        if isinstance(v, str):
            return v
    return None


def _find_source_root(module_file: Path) -> Path | None:
    """Walk up from the module looking for the tree's ``pyproject.toml``."""
    cur = module_file.parent
    for _ in range(_PYPROJECT_SEARCH_DEPTH):
        if (cur / "pyproject.toml").is_file():
            return cur
        if cur.parent == cur:  # hit the filesystem root
            break
        cur = cur.parent
    return None


def _find_dist_infos(module_file: Path, dist: str) -> list[Path]:
    """Every ``.dist-info`` directory for ``dist`` beside the installed package.

    More than one means the version is a COIN TOSS: ``importlib.metadata`` returns
    whichever it happens to find first, and that may not describe the code that is
    actually there. pip leaves these behind on some upgrade paths (observed
    2026-07-12: a 0.7.50 fossil left sitting next to a fresh 0.9.0).

    Returns ``[]`` when the layout cannot be inspected — and callers must treat
    that as "could not check", never as "there is only one".
    """
    # <site-packages>/<pkg>/__init__.py  ->  <site-packages>
    site_dir = module_file.parent.parent
    # PEP 503/427: `-` and `.` in the dist name become `_` in the directory name.
    normalized = dist.replace("-", "_").replace(".", "_")
    try:
        return [
            p
            for p in site_dir.iterdir()
            if p.is_dir()
            and p.name.endswith(".dist-info")
            and p.name.split("-", 1)[0].replace(".", "_") == normalized
        ]
    except OSError:
        return []


def _classify(module_file: Path) -> str:
    """wheel (under site-packages) vs editable (a source tree elsewhere)."""
    parts = set(module_file.parts)
    if any(marker in parts for marker in _SITE_MARKERS):
        return KIND_WHEEL
    return KIND_EDITABLE


def probe_install(
    dist: str,
    module: str | None = None,
    *,
    features: dict[str, str] | None = None,
) -> InstallProbe:
    """Probe ``dist``'s install and report whether its version string can be trusted.

    ``module`` defaults to ``dist`` with ``-`` mapped to ``_``.

    ``features`` optionally maps a label to a ``"module:attribute"`` path that
    should exist in the claimed version — a CONTENT probe. Use it to answer "is
    the code I think I deployed actually here?" without trusting any version at
    all::

        probe_install("scitex-cards", features={
            "blocked_check_v087": "scitex_cards._stale_active:detect_blocked_external",
        })

    Never raises. Any internal failure comes back in ``probe_error`` with a hint.
    """
    mod_name = module or dist.replace("-", "_")
    probe = InstallProbe(dist=dist, kind=KIND_ORPHANED)

    try:
        probe.metadata_version = _md.version(dist)
    except _md.PackageNotFoundError:
        probe.metadata_version = None
    except Exception as exc:  # noqa: BLE001 - a probe must never crash its caller
        probe.probe_error = f"reading metadata for {dist!r} failed: {exc}"

    try:
        mod = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001 - ImportError and anything __init__ raises
        probe.honest = False
        if probe.metadata_version is None:
            # Nothing claims to be installed and nothing imports. Not a lie —
            # just absent. Saying "orphaned .dist-info" here would send the
            # reader hunting for a directory that does not exist, which is the
            # very failure this module exists to stop: a confidently wrong hint.
            probe.kind = KIND_ABSENT
            probe.detail = (
                f"{dist} is not installed: no metadata, and `import {mod_name}` "
                f"failed ({exc})."
            )
            probe.hint = (
                f"Nothing to trust and nothing to fix — the package is simply "
                f"absent. Install it if it is expected: `pip install {dist}`."
            )
            return probe
        probe.kind = KIND_ORPHANED
        probe.detail = (
            f"metadata claims {dist} {probe.metadata_version}, but "
            f"`import {mod_name}` FAILED: {exc}"
        )
        # DO NOT recommend `--force-reinstall` here. It does NOT clear this.
        #
        # MEASURED by scitex-storage 2026-07-28, on the third occurrence in one
        # day: --force-reinstall removes only the files listed in the RECORD of
        # the dist-info it replaces, so an ORPHANED sibling survives untouched.
        # Their pass-by-pass numbers: one force-reinstall left 1 dist-info
        # (still broken, but it LOOKS like the fix ran), a second pass reached
        # 0, and only then did a plain install produce a clean tree.
        #
        # A hint that names a fix which does not clear the condition is worse
        # than a hint that names none: the reader spends their time on it AND
        # stops investigating, because the recommended action appeared to
        # succeed. This is the same failure family as a guard that cannot fire.
        probe.hint = (
            f"ORPHANED INSTALL — the WORST case: a .dist-info claims "
            f"{dist} {probe.metadata_version} with NO importable code behind it, so "
            f"every version check PASSES against a package that is not there.\n"
            f"    1. UNINSTALL REPEATEDLY until it reports nothing left to remove:\n"
            f"         while pip uninstall -y {dist}; do :; done\n"
            f"       ONE pass is NOT enough — each `pip uninstall` removes the "
            f"files of ONE dist-info, so a second (or third) copy survives a "
            f"single pass and the tree still looks broken afterwards.\n"
            f"    2. Confirm ZERO remain before reinstalling:\n"
            f"         ls -d <site-packages>/{dist.replace('-', '_')}-*.dist-info\n"
            f"    3. THEN install once: `pip install {dist}`\n"
            f"    Do NOT use `--force-reinstall`: it only removes files listed "
            f"in the RECORD of the dist-info it replaces, so the orphan survives."
        )
        return probe

    mod_file = getattr(mod, "__file__", None)
    if not mod_file:
        probe.kind = KIND_UNMANAGED
        probe.detail = f"{mod_name} has no __file__ (namespace package?)"
        probe.hint = "Cannot verify by content: the module exposes no file path."
        return probe

    mod_path = Path(mod_file).resolve()
    probe.module_path = str(mod_path)
    probe.kind = _classify(mod_path)

    if features:
        probe.features = {
            label: _has_feature(target) for label, target in features.items()
        }

    if probe.metadata_version is None:
        probe.kind = KIND_UNMANAGED
        probe.honest = True  # nothing is claimed, so nothing can lie
        probe.detail = (
            f"{mod_name} imports from {mod_path} but has NO installed metadata "
            f"(a bare sys.path entry). No version is claimed."
        )
        probe.hint = (
            "Honest by omission, but no version is knowable. If this package is "
            "meant to be deployed, install it properly so its version is reportable."
        )
        return probe

    if probe.kind == KIND_WHEEL:
        # A wheel install USUALLY means "the metadata arrived with the code beside
        # it, so it can be trusted". That was this module's original assumption —
        # and it is FALSE whenever pip leaves MORE THAN ONE .dist-info behind.
        #
        # Found 2026-07-12, by this probe FAILING on the first live install it was
        # pointed at. Upgrading scitex-cards 0.7.50 -> 0.9.0 in the agent venv left
        # BOTH directories in place:
        #     scitex_cards-0.9.0.dist-info/    (real, from the upgrade)
        #     scitex_cards-0.7.50.dist-info/   (orphaned fossil)
        # `importlib.metadata` picked the FOSSIL, and this function then declared
        # `trustworthy=True, code_version=0.7.50` — BOTH WRONG — while the code on
        # disk was 0.9.0. The probe built to catch lying installs BLESSED one.
        #
        # Only the `features` symbol probe told the truth. Hence this check: with
        # two dist-infos the version is AMBIGUOUS, and an ambiguous version is not
        # a trustworthy one. "I cannot tell which of these is real" must never
        # render as "it is fine" — the rule this module already claimed to follow.
        dist_infos = _find_dist_infos(mod_path, dist)
        if len(dist_infos) > 1:
            found = ", ".join(sorted(p.name for p in dist_infos))
            probe.honest = False
            probe.code_version = None  # genuinely unknown from metadata alone
            probe.detail = (
                f"AMBIGUOUS METADATA: {len(dist_infos)} .dist-info directories for "
                f"{dist} sit side by side ({found}). importlib.metadata picked "
                f"{probe.metadata_version}, but that is a COIN TOSS — the version "
                f"string cannot be trusted, and the code on disk may be any of them."
            )
            # DO NOT NAME A SINGLE DIRECTORY TO DELETE. The previous wording did,
            # picking `sorted(names)[0]`, and that is a LEXICOGRAPHIC sort over
            # version strings — so with 0.17.9 and 0.17.10 side by side it names
            # `...-0.17.10.dist-info` first, because "1" < "9". It told the reader
            # to delete the NEWER directory. Measured 2026-07-28 against exactly
            # those two versions, both released that day, so it was reachable in
            # practice and not a hypothetical.
            #
            # The fix is not a smarter sort. It is to stop guessing which one is
            # stale: remove them ALL and install once. That cannot pick wrong, and
            # it is the same procedure the ORPHANED branch prescribes, so a reader
            # who hits both does not have to reconcile two different rituals.
            #
            # `--force-reinstall` does NOT clear this either — it removes only the
            # files in the RECORD of the dist-info it replaces, leaving siblings.
            probe.hint = (
                f"pip left one or more stale .dist-info directories behind after "
                f"an upgrade. Every version check on this box is now a coin toss.\n"
                f"    1. Confirm WHICH code is really there with a symbol probe "
                f"(the `features` argument) — never with the version.\n"
                f"    2. UNINSTALL REPEATEDLY until nothing is left to remove:\n"
                f"         while pip uninstall -y {dist}; do :; done\n"
                f"       ONE pass is NOT enough: each `pip uninstall` clears ONE "
                f"dist-info, so with {len(dist_infos)} present a single pass leaves "
                f"{len(dist_infos) - 1} behind and the tree still looks broken.\n"
                f"    3. Confirm ZERO remain:\n"
                f"         ls -d <site-packages>/{dist.replace('-', '_')}-*.dist-info\n"
                f"    4. THEN install once: `pip install {dist}`\n"
                f"    Do NOT use `--force-reinstall`, and do NOT hand-delete one "
                f"directory hoping to guess the stale one."
            )
            return probe

        # Exactly one .dist-info: the metadata really does describe the code beside it.
        probe.code_version = probe.metadata_version
        probe.honest = True
        probe.detail = (
            f"{dist} {probe.metadata_version} installed as a wheel under "
            f"site-packages; metadata describes the code beside it."
        )
        return probe

    # Editable / bind-mounted: the metadata is a snapshot from install time and
    # the code has moved on freely since. Ask the SOURCE what it is.
    root = _find_source_root(mod_path)
    if root is None:
        probe.honest = False
        probe.detail = (
            f"{dist} loads from the source tree {mod_path} (editable/bound), but no "
            f"pyproject.toml was found above it, so the code's real version is "
            f"UNKNOWN. Metadata claims {probe.metadata_version} — do not trust it."
        )
        probe.hint = (
            "Verify by content instead: import a symbol that only exists in the "
            "version you expect (see the `features` argument)."
        )
        return probe

    probe.source_root = str(root)
    probe.code_version = _read_pyproject_version(root)

    if probe.code_version is None:
        probe.honest = False
        probe.detail = (
            f"{dist} loads from {root} (editable/bound) but its pyproject.toml "
            f"declares no static version; metadata claims {probe.metadata_version}, "
            f"which cannot be confirmed."
        )
        probe.hint = (
            "Cannot confirm by content. Either declare a static `project.version`, "
            "or verify with the `features` argument."
        )
        return probe

    probe.honest = probe.code_version == probe.metadata_version
    if probe.honest:
        probe.detail = (
            f"{dist} {probe.code_version} — editable/bound from {root}; metadata "
            f"agrees with the source. Version string is trustworthy."
        )
        return probe

    probe.detail = (
        f"VERSION STRING LIES: metadata says {dist} {probe.metadata_version}, but "
        f"the code actually loaded from {root} is {probe.code_version}."
    )
    probe.hint = (
        f"The .dist-info is a FOSSIL — it outlived the code it describes, so every "
        f"version-based deploy/drift check against {dist} is currently meaningless "
        f"(it will cry stale on a current install, or bless a stale one). Refresh "
        f"the metadata without touching anything else:\n"
        f"    uv pip install -e {root} --no-deps      (or: pip install -e {root} --no-deps)\n"
        f"Then re-probe. Until then, trust ONLY content checks, never the version."
    )
    return probe


def _has_feature(target: str) -> bool:
    """True when ``module:attribute`` exists — the content probe primitive."""
    mod_name, _, attr = target.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001
        return False
    if not attr:
        return True
    return hasattr(mod, attr)


def check_install_honest(dist: str = "scitex-cards") -> dict[str, object]:
    """Health-doctor check: is ``dist``'s reported version actually true?

    Returns the doctor's ``{ok, detail, hint}`` contract. ``ok`` is False exactly
    when the version string cannot be trusted — an orphaned install, or metadata
    that has drifted from the code it claims to describe.
    """
    probe = probe_install(dist)
    return {
        "ok": probe.trustworthy,
        "detail": probe.detail,
        "hint": probe.hint,
    }
