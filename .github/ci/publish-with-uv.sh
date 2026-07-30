#!/usr/bin/env bash
# Publishes ./dist/* to PyPI via MANUAL OIDC Trusted Publishing + twine,
# using a uv-provisioned Python on the bare self-hosted runner.
#
# Successor to publish-in-sif.sh (2026-07-11): the shared ci-cpu.sif vanished
# from the Spartan runners (incident-ci-sif-vanished-release-blocked-20260711)
# and the SIF's original rationale — "no Python on the bare node" — is
# obsolete: the org-level pytest-matrix reusable workflow already provisions
# Python on these same runners with astral-sh/setup-uv, green for weeks.
# The OIDC exchange below is UNCHANGED from publish-in-sif.sh; only where
# Python comes from differs. PyPI's trusted-publisher config keys on
# (owner, repo, workflow file name), none of which change here.
#
# WHY manual OIDC (not pypa/gh-action-pypi-publish): that action is a Docker
# container action and the Spartan compute nodes have no Docker. Trusted
# Publishing is just an OIDC token exchange over HTTPS:
#   1. JWT (audience=pypi) from the Actions OIDC provider, via the per-job
#      ACTIONS_ID_TOKEN_REQUEST_{TOKEN,URL} env vars (present because the
#      publish job declares `permissions: id-token: write`).
#   2. Exchange the JWT at PyPI's mint-token endpoint for a short-lived token.
#   3. twine upload dist/* with TWINE_USERNAME=__token__.
#
# Fail-loud (operator directive): set -euo pipefail; every step asserts
# non-empty output; failures carry the exact cause, never a silent skip.
set -euo pipefail

command -v uv >/dev/null || {
    echo "::error::uv not on PATH — the workflow must run astral-sh/setup-uv before this script"
    exit 1
}

# Provision the interpreter (downloads a standalone CPython if the runner has
# none — the whole point of retiring the SIF).
uv venv --python 3.12 .venv-publish
PY=".venv-publish/bin/python"
test -x "$PY" || {
    echo "::error::uv venv did not produce $PY"
    exit 1
}

export LC_ALL=C.UTF-8 LANG=C.UTF-8

if [ ! -d dist ] || [ -z "$(ls -A dist 2>/dev/null)" ]; then
    echo "::error::dist/ is empty — nothing to publish (download the build artifact first)"
    exit 1
fi
echo "=== dist to publish ==="
ls -la dist/

# --- step 1: request the OIDC JWT (audience=pypi) from GitHub ---
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?not set — the publish job needs 'permissions: id-token: write'}"
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?not set — the publish job needs 'permissions: id-token: write'}"

echo "=== minting OIDC JWT (audience=pypi) ==="
JWT="$(curl -fsS \
    -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
    "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=pypi" |
    "$PY" -c 'import sys,json; print(json.load(sys.stdin)["value"])')"
test -n "$JWT" || {
    echo "::error::OIDC JWT request returned an empty token"
    exit 1
}
echo "OIDC JWT obtained (length=${#JWT})"

# --- step 2: exchange the JWT for a short-lived PyPI API token ---
echo "=== exchanging JWT at PyPI mint-token endpoint ==="
MINT_RESP="$(curl -sS -X POST https://pypi.org/_/oidc/mint-token \
    -d "{\"token\":\"${JWT}\"}")"
MINTED="$(printf '%s' "$MINT_RESP" |
    "$PY" -c 'import sys,json; d=json.load(sys.stdin); print(d.get("token",""))')"
if [ -z "$MINTED" ]; then
    # Surface PyPI's error body VERBATIM (the JWT is NOT echoed) so a trust
    # misconfiguration is diagnosable.
    echo "::error::PyPI mint-token returned no token."
    echo "--- PyPI mint-token response body (raw) ---"
    printf '%s\n' "$MINT_RESP"
    echo "--- (pretty, best-effort) ---"
    printf '%s' "$MINT_RESP" |
        "$PY" -c 'import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))' \
            2>/dev/null || true
    exit 1
fi
echo "PyPI token minted (length=${#MINTED})"

# --- step 3: twine upload ---
echo "=== installing twine into the publish venv ==="
uv pip install --python "$PY" twine

# --skip-existing makes the publish IDEMPOTENT: re-running a tag, or tagging a
# version whose files already reached PyPI, skips those files instead of
# failing the job. Without it the only outcomes are "published" and "red", and
# a version can only be published once — so ANY re-run is permanently red.
#
# This is not hypothetical: 0.17.0 was published out-of-band with a manual
# `twine upload` (my mistake — the tag-triggered workflow already existed and
# I did not read it), which left the correct path unable to run at all without
# going red. A red run that means "already done" is a drift detector turned
# off: it trains the reader to skip release failures, and the next one that
# means something real gets skipped too.
#
# It does NOT weaken the gate. A genuinely failed upload — bad token, network,
# rejected metadata — still fails loudly; only an ALREADY-PRESENT file is
# skipped, and that file is byte-identical by PyPI's own immutability rule.
echo "=== twine upload dist/* (--skip-existing: publishing is idempotent) ==="
TWINE_USERNAME="__token__" TWINE_PASSWORD="$MINTED" \
    "$PY" -m twine upload --non-interactive --disable-progress-bar --skip-existing dist/*

echo "PUBLISH-OK: dist/* uploaded to PyPI via manual OIDC trusted publishing (uv-provisioned python)"
