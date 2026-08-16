#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Shared helpers for the scitex-cards git->card hooks (post-commit, pre-push).
#
# Phase P3 of the task-driven-feedback epic (card tcfb-p3-git-to-card).
# These helpers capture a local git mutation onto the matching board
# card's ROUTE by emitting a canonical "push" event to
#   scitex-cards hook push --payload -
#
# SOFT linking: a card is annotated only when a card id is present in the
# branch name (or a `Card: <id>` commit-message trailer). Ad-hoc commits
# with no card id are NOT an error -- the hooks emit nothing and exit 0.
#
# BEST EFFORT: every step is guarded so a hook NEVER blocks the
# commit/push, even if python, the package, or the consumer is missing.
#
# This file is sourced, not executed; it defines functions only.

# Resolve a python interpreter that can import scitex_cards. Preference:
#   1. repo-local venv (.venv/bin/python) -- CI-parity dev setup
#   2. `scitex-cards` console-script's interpreter (best proxy on PATH)
#   3. plain python3
# Echoes the interpreter path; callers test importability separately.
sttc_python() {
    local repo_root="$1"
    if [ -x "${repo_root}/.venv/bin/python" ]; then
        echo "${repo_root}/.venv/bin/python"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"
        return 0
    fi
    echo "python"
}

# Derive an "owner/repo" slug from the origin remote URL. Handles both
# SSH (git@github.com:owner/repo.git) and HTTPS
# (https://github.com/owner/repo.git) forms. Echoes "" if undeterminable.
sttc_repo_slug() {
    local repo_root="$1"
    local url
    url="$(git -C "${repo_root}" remote get-url origin 2>/dev/null)" || return 0
    [ -n "${url}" ] || return 0
    # Strip a trailing .git, then take the last two path segments.
    url="${url%.git}"
    # Normalise scp-like "host:owner/repo" to ".../owner/repo".
    url="${url/://}"
    # Echo the trailing owner/repo (last two slash-separated segments).
    echo "${url}" | awk -F/ '{ if (NF>=2) printf "%s/%s", $(NF-1), $NF }'
}

# Emit + dispatch a push event for one commit, best-effort.
# Args: repo_root branch commit_sha [commit_msg_file] [trigger]
# - trigger (commit|push, default push) tells the consumer whether this
#   came from post-commit (commit) or pre-push (push) so it emits the
#   matching canonical card-event type (committed vs pushed).
# - Resolves the card id (branch, then Card: trailer in the msg file).
# - If a card id resolves, builds the event JSON in python and pipes it
#   to `scitex-cards hook push --payload -`.
# - No card id  -> silent exit 0 (SOFT).
# - Any failure -> swallowed (|| true); the hook never blocks git.
sttc_emit_push() {
    local repo_root="$1" branch="$2" sha="$3" msg_file="${4:-}" trigger="${5:-push}"
    [ -n "${branch}" ] || return 0
    [ -n "${sha}" ] || return 0

    local py slug author event
    py="$(sttc_python "${repo_root}")"

    # If the interpreter can't import scitex_cards, give up softly.
    "${py}" -c "import scitex_cards._git_link" >/dev/null 2>&1 || return 0

    slug="$(sttc_repo_slug "${repo_root}")"
    author="$(git -C "${repo_root}" log -1 --format='%an' "${sha}" 2>/dev/null)"

    local -a msg_args=()
    if [ -n "${msg_file}" ] && [ -f "${msg_file}" ]; then
        msg_args=(--message-file "${msg_file}")
    fi

    # Build the canonical push-event JSON in python (safe quoting of the
    # commit message). Empty stdout == no card id == SOFT skip.
    event="$("${py}" -m scitex_cards._git_link emit-event \
        --repo "${slug}" \
        --branch "${branch}" \
        --sha "${sha}" \
        --author "${author}" \
        --trigger "${trigger}" \
        "${msg_args[@]}" 2>/dev/null)" || return 0
    [ -n "${event}" ] || return 0

    # Pipe to the consumer. Prefer the repo-venv console script; fall back
    # to `scitex-cards` on PATH, then to `python -m scitex_cards`.
    if [ -x "${repo_root}/.venv/bin/scitex-cards" ]; then
        printf '%s\n' "${event}" |
            "${repo_root}/.venv/bin/scitex-cards" hook push --payload - \
                >/dev/null 2>&1 || true
    elif command -v scitex-cards >/dev/null 2>&1; then
        printf '%s\n' "${event}" |
            scitex-cards hook push --payload - >/dev/null 2>&1 || true
    else
        printf '%s\n' "${event}" |
            "${py}" -m scitex_cards hook push --payload - \
                >/dev/null 2>&1 || true
    fi
    return 0
}

# EOF
