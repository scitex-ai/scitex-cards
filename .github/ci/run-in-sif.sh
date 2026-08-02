#!/usr/bin/env bash
# Runs INSIDE the reused scitex-ci SIF (apptainer exec). $1 = python version.
#
# WHY a layered install (not the bare PYTHONPATH=src trick scitex-dev uses):
# the shared ci-cpu.sif bakes scitex-dev[all,dev] DEPS, NOT scitex-todo's —
# matplotlib / graphviz / seaborn / django / Pillow / networkx / playwright /
# pytesseract / scitex-app / scitex-ui are absent from the SIF. So we install
# THIS checkout + its [all,dev] extras (WITH dependency resolution) into a
# writable --target dir and prepend that on PYTHONPATH. The SIF still supplies
# the heavy shared base (pip/uv, the python interpreters, scitex-dev's deps),
# so only scitex-todo's own thin dep set is fetched per run.
#
# --target (not a plain `-e .`): the SIF's /opt/venv-* are root-owned + RO and
# the HPC compute-node HOME is RO inside the container, so a normal site install
# fails Permission denied. A writable target on node-local /tmp sidesteps both.
#
# Fail-loud: a missing interpreter or a failed install is a hard error.
set -euo pipefail

V="${1:?python version arg required (3.11/3.12/3.13)}"
VENV="/opt/venv-$V"
test -x "$VENV/bin/python" || {
    echo "::error::baked python missing in $VENV — rebuild the SIF: scitex-container apptainer build ci-cpu"
    exit 1
}

export LC_ALL=C.UTF-8 LANG=C.UTF-8

# Real writable scratch. The runner profile exports TMPDIR=~/.cache/tmp, a host
# path that does NOT resolve inside the container; tests (tmp_path) and the
# install target both need a working, writable tmp. Node-local /tmp is writable
# + ephemeral and per-version-isolated so concurrent matrix legs don't collide.
export TMPDIR="/tmp/ci-scitex_cards-${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-0}-$V"
rm -rf "$TMPDIR"
mkdir -p "$TMPDIR/site" "$TMPDIR/uv-cache"

# The HPC compute-node $HOME is READ-ONLY inside the container, so uv/pip cannot
# create their default caches under ~/.cache — point them at the writable
# scratch instead (else `uv pip install` dies: "failed to create directory
# ~/.cache/uv: File exists / read-only").
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export XDG_CACHE_HOME="$TMPDIR"
export PIP_CACHE_DIR="$TMPDIR/pip-cache"

# Headless matplotlib — no DISPLAY on the compute node; force the Agg backend so
# pyplot imports + figure rendering in the test suite never try to open a GUI.
export MPLBACKEND=Agg

# Dedicated, stable matplotlib config/cache dir for this matrix leg. Without
# pinning it, MPLCONFIGDIR defaults to $XDG_CACHE_HOME/matplotlib which is COLD
# every CI run; the xdist workers (one per core, see below) then each cold-start
# matplotlib and RACE to build fontList.json in that shared dir.
# A partial/contended cache makes some renders fall back to a different font, so
# scitex-todo's reproducibility tests (validate_recipe renders the SAME recipe
# twice and compares) see render1 != render2 → spurious MSE-over-threshold
# failures (e.g. TestValidateRecipe, max channel diff 255). One stable dir +
# a single warm-up below (build the cache ONCE, pre-fork) removes the race.
export MPLCONFIGDIR="$TMPDIR/mpl"
mkdir -p "$MPLCONFIGDIR"

# A VIRTUAL_ENV leaked from the runner profile (~/.env-3.11) is a broken symlink
# in here; unset it so no tool (uv, pip) tries to follow it.
unset VIRTUAL_ENV || true

# venv bin on PATH (this matrix leg's python3 + pip); PYTHONPATH points at the
# writable target so imports + coverage use the freshly-installed checkout.
export PATH="$VENV/bin:$PATH"

echo "py=$("$VENV/bin/python" -V) target=$TMPDIR/site"

# Install scitex-cards + every runtime extra + the dev toolchain into the
# writable target.
#
# ADR-0005: `dev` is a PEP 735 dependency GROUP, not an extra, so it is
# requested with --group. `.[dev]` NO LONGER RESOLVES and would fail here.
#
# The fallback chain degrades to LESS TOOLING, never to fewer runtime
# capabilities — `.[all]` is present in every rung that installs extras at all.
# That direction is the point: this repo's outage came from an install that
# silently omitted a runtime capability, so a chain that could fall back to a
# partial runtime set would reintroduce it.
#
# pip is NOT a --group fallback: PEP 735 support landed in pip 25.1 and the
# runner's pip may predate it, so the pip rung deliberately installs the
# runtime set only rather than appearing to install a toolchain it cannot.
uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e ".[all]" --group dev ||
    uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e ".[all]" ||
    uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e "." ||
    pip install --target="$TMPDIR/site" -e ".[all]"

export PYTHONPATH="$TMPDIR/site:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

# Run SINGLE-PROCESS — deliberately NO pytest-xdist here. This mirrors the
# required `tests` (pytest-matrix) gate, which runs `pytest tests/` with the
# pyproject addopts (`-x -q`, no `-n`) and is the proven-green path on every
# tagged commit. Full `-n <all-cores>` xdist saturates the node and STARVES the
# suite's concurrency tests that spawn their own subprocesses — e.g.
# test_two_concurrent_writers_serialize_via_flock (30s subprocess timeout) and
# the comment turn-URL timing test — which then time out non-deterministically
# and blocked the v0.7.29 release even though the gate was green on the same
# commit. Single-process removes that contention; the gate already parallelises
# CI throughput across the 3 matrix legs, so the release re-test just needs to
# be reliable, not fast.

# Warm the matplotlib font cache ONCE before the run. This builds
# $MPLCONFIGDIR/fontlist-*.json so tests read a complete, consistent cache
# (the source of the render1!=render2 reproducibility flakes). Fail-loud: if
# matplotlib can't even build its font cache, CI must surface it. matplotlib
# may not be a dependency of this package; only warm the font cache when it's
# importable (no-op otherwise — never fail the run on an optional warm-up).
if python -c "import matplotlib" 2>/dev/null; then
  python -c "import matplotlib; matplotlib.use('Agg'); from matplotlib import font_manager; font_manager.fontManager; import matplotlib.pyplot as plt; f=plt.figure(); f.canvas.draw(); print('mpl font cache warmed at', matplotlib.get_cachedir())"
else
  echo "matplotlib not importable — skipping font-cache warm-up (not a dep)"
fi

# nice -n 19 ionice -c 3: run at the lowest CPU + idle I/O priority so that if
# this node is ever shared with interactive/dev work, CI yields the CPU and
# disk to any higher-priority process. exec replaces the shell with nice,
# which execs ionice, which execs python (still PID-traceable, signals/exit
# code propagate to the runner step). `-q` overrides pyproject's verbosity to
# keep the CI log compact.
exec nice -n 19 ionice -c 3 \
    python -m pytest tests/ -q \
    --cov=src/scitex_cards --cov-report=xml --cov-report=term \
    -p no:cacheprovider
