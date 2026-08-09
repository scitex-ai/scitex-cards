#!/usr/bin/env python3
"""Resolve a workspace IDENTITY to its store. The one place tenancy is enforced.

WHY IDENTITY AND NOT A PATH. scitex-hub knows which workspace a request belongs
to - that is genuinely hub's fact, because hub owns orgs, projects and users. It
does NOT know where cards keeps data, and when it did know (it injected
``base/<slug>/.scitex/todo/tasks.yaml``) my store migration broke it. So hub passes
``request.scitex_workspace``, an identity, and the identity -> store mapping lives
here, once.

scitex-db's argument, which decided it against my own earlier proposal of a base
DIRECTORY: Postgres is not a distant prospect, it is this morning - the host
PostgreSQL went 14 -> 18.4 as the migration destination and their toolkit is merged.
A "base directory" has no meaning against Postgres, where a workspace is a schema or
a row scope. Naming the field for today's storage mechanism would force a second
rename on both sides within weeks.

=========================================================================
WHY THE VALIDATOR IS AN ALLOWLIST, WHICH IS STRICTER THAN I WAS ASKED FOR
=========================================================================
scitex-db specified: reject anything that looks like a location - contains ``/``, or
ends ``.db`` / ``.yaml``. That is a DENYLIST, and this function is the single point
where tenant isolation is enforced (hub reports that isolation is the operator's
first security requirement). A denylist at a security boundary fails by omission:
every traversal bug in history is a denylist that did not think of one more encoding.
``..`` alone contains no slash. ``%2e%2e%2f`` contains no literal slash either. A
trailing space, a NUL, a Windows separator, a unicode homoglyph - each is a separate
thing to remember.

So identities must MATCH a slug shape and everything else is refused. The set of
things that get through is then finite and inspectable, rather than being whatever
nobody thought to ban.

FAIL CLOSED, also scitex-db's requirement and the same rule as the canonical-store
refusal: an unknown or unusable workspace RAISES. It never resolves to a default, an
empty store, or the ambient store. "I could not tell which workspace" must not
collapse into "here is somebody's data" - which under multi-tenancy is not merely a
wrong answer, it is a disclosure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ._store_errors import StoreNotProvisionedError, StoreUnavailableError

__all__ = [
    "ENV_WORKSPACE_ROOT",
    "InvalidWorkspaceIdentity",
    "is_valid_identity",
    "resolve_workspace_store",
]

#: Where workspaces live. Deployment configuration, so it is MINE (or sac's) to set
#: - never something a request may influence, or a caller could point the resolver
#: at a tree of its choosing.
ENV_WORKSPACE_ROOT = "SCITEX_CARDS_WORKSPACE_ROOT"

#: Lowercase alphanumeric, with internal ``-`` or ``_``. No dots (so ``..`` cannot
#: form), no separators, no leading punctuation. Anchored at both ends, so a
#: multiline payload cannot smuggle a second value past a partial match.
_IDENTITY_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,62}\Z")


class InvalidWorkspaceIdentity(ValueError):
    """The caller passed something that is not a workspace identity.

    A ValueError rather than a StoreUnavailableError: this is a CALLER fault - a
    wrong kind of value - not an unavailable store. Distinguishing them lets the
    Django layer answer 400 for one and 500 for the other, and lets a reviewer see
    which side of the contract broke.
    """


def is_valid_identity(value: object) -> bool:
    """Whether ``value`` is a well-formed workspace identity.

    Pure and side-effect free, so the validator can be tested exhaustively without
    a filesystem - which matters, because the interesting inputs are hostile ones.
    """
    return isinstance(value, str) and bool(_IDENTITY_RE.match(value))


def resolve_workspace_store(identity: object) -> Path:
    """The canonical store for ``identity``. RAISES rather than guessing.

    Raises:
        InvalidWorkspaceIdentity: ``identity`` is not slug-shaped - including any
            path, any traversal attempt, and any non-string.
        StoreUnavailableError: the root is unconfigured, or that workspace has no
            store. Never falls back to the ambient store: under multi-tenancy a
            fallback would serve one tenant another tenant's data.
    """
    if not is_valid_identity(identity):
        # Deliberately does NOT echo the value: this text can reach a log that a
        # third party reads, and a rejected identity may itself be an injection
        # attempt worth not repeating. The type and length are enough to debug a
        # legitimate mistake.
        raise InvalidWorkspaceIdentity(
            f"workspace identity must match {_IDENTITY_RE.pattern} "
            f"(got {type(identity).__name__} of length "
            f"{len(identity) if isinstance(identity, str) else 'n/a'}). "
            f"A filesystem path is not an identity - pass the workspace slug."
        )

    root = os.environ.get(ENV_WORKSPACE_ROOT, "").strip()
    if not root:
        raise StoreUnavailableError(
            f"{ENV_WORKSPACE_ROOT} is not set, so a per-workspace store cannot be "
            f"resolved. REFUSING to fall back to the ambient store: under "
            f"multi-tenancy that would serve one workspace another's cards."
        )

    store = Path(root).expanduser() / identity / ".scitex" / "cards" / "cards.db"

    # BELT AND BRACES over the allowlist. The regex already makes traversal
    # impossible, so this can only fire if the regex is later loosened - which is
    # exactly when a second check earns its keep. Cheap, and it fails closed.
    resolved_root = Path(root).expanduser().resolve()
    if resolved_root not in store.resolve().parents:
        raise InvalidWorkspaceIdentity(
            "resolved store escapes the workspace root - refusing"
        )

    if not store.exists():
        # NOT-PROVISIONED, and on this path it is the ORDINARY case rather than
        # an exceptional one: a workspace that has never been set up is what
        # every new tenant looks like. The root being unset (above) stays the
        # parent type — that is OUR deployment misconfigured, not their tenancy
        # being new, and it must not render an onboarding page to everyone.
        raise StoreNotProvisionedError(
            f"workspace store {store} does not exist. REFUSING to continue: a "
            f"missing database reads back as an empty document, and that value is "
            f"written back as the WHOLE store. Provision the workspace rather than "
            f"letting cards invent one."
        )
    return store


# EOF
