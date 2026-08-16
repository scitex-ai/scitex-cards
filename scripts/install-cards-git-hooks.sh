#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Install the scitex-cards git->card hooks into a repository.
#
# Phase P3 of the task-driven-feedback epic (card tcfb-p3-git-to-card).
# Wires the `post-commit` + `pre-push` hooks (from this package's
# `.githooks/` dir) so that local git mutations on a `<type>/<card-id>-*`
# branch are recorded on the matching scitex-cards card's ROUTE via
#   scitex-cards hook push --payload -
#
# SOFT linking: commits on ad-hoc branches (no card id) are skipped
# silently by the hooks; this installer just wires them in.
#
# Idempotent: re-running is safe -- it re-points `core.hooksPath` (or
# re-copies the hooks) without duplicating anything.
#
# Usage:
#   scripts/install-cards-git-hooks.sh [TARGET_REPO]
#     TARGET_REPO   repo to install into (default: git root of $PWD)
#
#   --copy          copy the hooks into <repo>/.githooks instead of
#                   pointing core.hooksPath at this package's tree (use
#                   when the package tree is not co-located with the repo,
#                   e.g. installed from a wheel into a venv).
#   --uninstall     remove the wiring (unset core.hooksPath when it points
#                   at our dir; never touches an unrelated hooksPath).
#   -h | --help     show this help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `.githooks/` lives at the package/repo root, one level up from scripts/.
SRC_HOOKS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/.githooks"

MODE="link"
TARGET=""

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
    --copy) MODE="copy" ;;
    --uninstall) MODE="uninstall" ;;
    -h | --help)
        usage
        exit 0
        ;;
    -*)
        echo "install-cards-git-hooks: unknown option: $1" >&2
        exit 2
        ;;
    *)
        TARGET="$1"
        ;;
    esac
    shift
done

# Resolve the target repo root.
if [ -n "${TARGET}" ]; then
    REPO_ROOT="$(git -C "${TARGET}" rev-parse --show-toplevel 2>/dev/null)" || {
        echo "install-cards-git-hooks: not a git repo: ${TARGET}" >&2
        exit 1
    }
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        echo "install-cards-git-hooks: run inside a git repo (or pass TARGET)" >&2
        exit 1
    }
fi

HOOK_NAMES="post-commit pre-push"

if [ "${MODE}" = "uninstall" ]; then
    current="$(git -C "${REPO_ROOT}" config --local --get core.hooksPath 2>/dev/null || true)"
    if [ -n "${current}" ] && [ "${current}" = "${SRC_HOOKS_DIR}" ]; then
        git -C "${REPO_ROOT}" config --local --unset core.hooksPath
        echo "install-cards-git-hooks: unset core.hooksPath (was our dir)"
    elif [ -n "${current}" ] && [ "$(basename "${current}")" = ".githooks" ]; then
        git -C "${REPO_ROOT}" config --local --unset core.hooksPath
        echo "install-cards-git-hooks: unset core.hooksPath (${current})"
    else
        echo "install-cards-git-hooks: core.hooksPath not set by us; nothing to undo"
    fi
    # Remove any copied hooks we own (best-effort; only those carrying our
    # marker line so we never delete an unrelated hook of the same name).
    for name in ${HOOK_NAMES}; do
        f="${REPO_ROOT}/.githooks/${name}"
        if [ -f "${f}" ] && grep -q "scitex-cards git->card hook" "${f}" 2>/dev/null; then
            rm -f "${f}"
        fi
    done
    exit 0
fi

if [ ! -d "${SRC_HOOKS_DIR}" ]; then
    echo "install-cards-git-hooks: source hooks dir not found: ${SRC_HOOKS_DIR}" >&2
    echo "  (re-run with --copy from a checkout, or reinstall the package)" >&2
    exit 1
fi

# Refuse to clobber an unrelated existing core.hooksPath silently.
current="$(git -C "${REPO_ROOT}" config --local --get core.hooksPath 2>/dev/null || true)"
if [ -n "${current}" ] &&
    [ "${current}" != "${SRC_HOOKS_DIR}" ] &&
    [ "$(basename "${current}")" != ".githooks" ]; then
    echo "install-cards-git-hooks: core.hooksPath already set to '${current}'." >&2
    echo "  Refusing to overwrite. Merge our hooks into that dir, or unset it" >&2
    echo "  first: git -C '${REPO_ROOT}' config --unset core.hooksPath" >&2
    exit 1
fi

if [ "${MODE}" = "link" ]; then
    git -C "${REPO_ROOT}" config --local core.hooksPath "${SRC_HOOKS_DIR}"
    echo "install-cards-git-hooks: core.hooksPath -> ${SRC_HOOKS_DIR}"
    echo "  hooks active: ${HOOK_NAMES}"
    exit 0
fi

# MODE=copy -- materialise the hooks into <repo>/.githooks and point at it.
DST_HOOKS_DIR="${REPO_ROOT}/.githooks"
mkdir -p "${DST_HOOKS_DIR}"
cp -f "${SRC_HOOKS_DIR}/_lib.sh" "${DST_HOOKS_DIR}/_lib.sh"
for name in ${HOOK_NAMES}; do
    cp -f "${SRC_HOOKS_DIR}/${name}" "${DST_HOOKS_DIR}/${name}"
    chmod +x "${DST_HOOKS_DIR}/${name}"
done
chmod +x "${DST_HOOKS_DIR}/_lib.sh" 2>/dev/null || true
git -C "${REPO_ROOT}" config --local core.hooksPath "${DST_HOOKS_DIR}"
echo "install-cards-git-hooks: copied hooks into ${DST_HOOKS_DIR}"
echo "  core.hooksPath -> ${DST_HOOKS_DIR}"
echo "  hooks active: ${HOOK_NAMES}"
exit 0
# EOF
