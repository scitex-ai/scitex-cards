#!/usr/bin/env python3
"""Resolve a workspace IDENTITY to its store. The one place tenancy is enforced.

WHY IDENTITY AND NOT A LOCATION. scitex-hub knows which workspace a request
belongs to - that is genuinely hub's fact, because hub owns orgs, projects and
users. It does NOT know where cards keeps data, and when it did know (it injected
a per-slug path) my store migration broke it. So hub passes
``request.scitex_workspace``, an identity, and the identity -> store mapping lives
here, once.

scitex-db's argument, which decided it against my own earlier proposal of a base
DIRECTORY: a "base directory" has no meaning against PostgreSQL, where a
workspace is a schema or a row scope. Naming the field for a storage mechanism
would force a rename on both sides within weeks. That prediction is now spent:
a workspace IS a schema, and this module is what says so.

=========================================================================
WHY A SCHEMA, AND NOT A ROW SCOPE
=========================================================================
ADR-0017 fixes it: a tenant is a STORE, not a row - the authority boundary IS
the handle a caller holds. A row scope makes isolation a property of every
query's WHERE clause, so one forgotten predicate is a cross-tenant read. A
schema makes it a property of the CONNECTION: the handle this module returns
cannot address another tenant's tables, because they are not on its
``search_path``. The guarantee moves from "every author remembers" to "the bad
state is not expressible", which is the same reasoning
:mod:`scitex_cards._backend_connect` uses for paramstyle.

=========================================================================
WHY THE VALIDATOR IS AN ALLOWLIST, WHICH IS STRICTER THAN I WAS ASKED FOR
=========================================================================
scitex-db specified: reject anything that looks like a location - contains ``/``,
or ends ``.db`` / ``.yaml``. That is a DENYLIST, and this function is the single
point where tenant isolation is enforced (hub reports that isolation is the
operator's first security requirement). A denylist at a security boundary fails
by omission: every traversal bug in history is a denylist that did not think of
one more encoding. ``..`` alone contains no slash. ``%2e%2e%2f`` contains no
literal slash either. A trailing space, a NUL, a Windows separator, a unicode
homoglyph - each is a separate thing to remember.

So identities must MATCH a slug shape and everything else is refused. The set of
things that get through is then finite and inspectable, rather than being
whatever nobody thought to ban.

FAIL CLOSED, also scitex-db's requirement and the same rule as the
canonical-store refusal: an unknown or unusable workspace RAISES. It never
resolves to a default, an empty store, or the ambient store. "I could not tell
which workspace" must not collapse into "here is somebody's data" - which under
multi-tenancy is not merely a wrong answer, it is a disclosure.

=========================================================================
WHY THE IDENTITY IS SEGMENTS AND NOT ONE SLUG
=========================================================================
scitex-hub's tenancy is TWO-dimensional - a tenant is ``(owner, project)``, and
the owner is itself two namespaces (users and orgs, separate roots). One flat
segment cannot express that, and the obvious workaround is the dangerous one.
hub measured it before building against it:

    owner "alice-my" + project "project"    ->  alice-my-project
    owner "alice"    + project "my-project" ->  alice-my-project

Two tenants, one identity, one store. An identity collision IS a cross-tenant
read, reached THROUGH the sanctioned primitive rather than around it, which is
worse than reaching it around one because it looks compliant.

THE SEPARATOR IS CHOSEN FROM WHAT THE ALLOWLIST FORBIDS, which is what makes it
safe rather than merely conventional. The collision above exists because ``-``
is legal INSIDE a segment, so the join is ambiguous. ``/`` is not legal inside a
segment - :data:`_IDENTITY_RE` admits only ``[a-z0-9_-]`` - so ``"/".join`` has
exactly one parse and the two shapes above map to different pre-images. The
property is inherited from the validator rather than asserted here, so loosening
the validator to admit ``/`` would be caught by
``test_a_separator_inside_a_segment_is_refused`` rather than silently
reintroducing the collision.

Arity is the CALLER's business; validity is not. A one-dimensional consumer
passes one segment and sees the previous behaviour exactly.
"""

from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ._store_errors import StoreNotProvisionedError, StoreUnavailableError

__all__ = [
    "ENV_WORKSPACE_DB",
    "InvalidWorkspaceIdentity",
    "is_valid_identity",
    "provision_workspace_store",
    "resolve_workspace_store",
]

#: The cluster workspaces live in, as a DSN. Deployment configuration, so it is
#: MINE (or sac's) to set - never something a request may influence, or a caller
#: could point the resolver at a database of its choosing.
ENV_WORKSPACE_DB = "SCITEX_CARDS_WORKSPACE_DB"

#: Which workspaces EXIST. The existence of a tenant is state, so it is a row
#: here rather than something inferred by probing the catalog: a schema can be
#: present because a half-finished provision created it, and "the directory is
#: there" was exactly the false positive the previous design kept hitting.
_REGISTRY_TABLE = "scitex_cards_workspaces"

#: Lowercase alphanumeric, with internal ``-`` or ``_``. No dots (so ``..`` cannot
#: form), no separators, no leading punctuation. Anchored at both ends, so a
#: multiline payload cannot smuggle a second value past a partial match.
_IDENTITY_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,62}\Z")

#: The join character. MUST stay outside :data:`_IDENTITY_RE`; see the module
#: docstring for why that is the whole guarantee and not a style choice.
_SEGMENT_SEPARATOR = "/"


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
    a server - which matters, because the interesting inputs are hostile ones.
    """
    return isinstance(value, str) and bool(_IDENTITY_RE.match(value))


def _validate(segments: tuple[object, ...]) -> None:
    """Refuse anything that is not an identity. Pure; raises or returns None."""
    if not segments:
        raise InvalidWorkspaceIdentity(
            "a workspace identity needs at least one segment; got none. "
            "Passing nothing would resolve to the workspace ROOT, which is "
            "every tenant's store at once."
        )

    for index, segment in enumerate(segments):
        if is_valid_identity(segment):
            continue
        # Deliberately does NOT echo the value: this text can reach a log that a
        # third party reads, and a rejected identity may itself be an injection
        # attempt worth not repeating. The position, type and length are enough
        # to debug a legitimate mistake, and the POSITION is what a caller
        # passing three segments actually needs.
        raise InvalidWorkspaceIdentity(
            f"workspace identity segment {index} must match "
            f"{_IDENTITY_RE.pattern} (got {type(segment).__name__} of length "
            f"{len(segment) if isinstance(segment, str) else 'n/a'}). "
            f"A location is not an identity - pass validated segments. "
            f"Uppercase is REFUSED rather than folded: folding would map "
            f"'Alice' and 'alice' to one store."
        )


def _schema_for(segments: tuple[object, ...]) -> str:
    """The schema ``segments`` map to, with every safety check and NO server call.

    Shared by both verbs so they cannot disagree about where a workspace lives.
    Two functions each computing the name independently is how ``resolve`` and
    ``provision`` end up pointing at different schemas after one of them is
    edited - and the symptom is a tenant whose store is provisioned somewhere
    the resolver will never look.

    A DIGEST RATHER THAN THE SEGMENTS THEMSELVES, for one hard reason: a
    PostgreSQL identifier is truncated at 63 bytes, silently. Each segment may
    itself be 63 characters, so any readable encoding of a multi-segment identity
    can overflow - and truncation maps two identities onto one schema, which is
    precisely the collision this module exists to make unrepresentable. A
    fixed-width digest cannot overflow, so it cannot be truncated.

    DETERMINISTIC, not allocated. The mapping is recomputable from the identity
    alone, so a lost registry row is a recoverable bookkeeping fault rather than
    an orphaned tenant whose data nobody can name any more.
    """
    _validate(segments)
    preimage = _SEGMENT_SEPARATOR.join(str(segment) for segment in segments)
    return "ws_" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()[:32]


def _cluster_dsn() -> str:
    """The DSN workspaces are carved from, or a refusal naming what to set."""
    dsn = os.environ.get(ENV_WORKSPACE_DB, "").strip()
    if not dsn:
        raise StoreUnavailableError(
            f"{ENV_WORKSPACE_DB} is not set, so a per-workspace store cannot be "
            f"resolved. REFUSING to fall back to the ambient store: under "
            f"multi-tenancy that would serve one workspace another's cards."
        )
    return dsn


def _workspace_dsn(cluster: str, schema: str) -> str:
    """``cluster`` scoped to ``schema`` via ``search_path``.

    The handle IS the boundary (ADR-0017): an unqualified statement made on this
    DSN lands inside the tenant's schema, and a tenant's tables are not reachable
    from another tenant's handle without naming the schema explicitly.

    MERGES INTO AN EXISTING ``options``, rather than appending a second one.
    libpq does not concatenate repeated URI parameters - the last occurrence
    WINS OUTRIGHT - so appending would silently discard whatever the deployment
    had already put there (a statement_timeout, an application_name). Merging
    keeps those and places ours last, which is what makes the tenant's
    search_path the effective one.
    """
    setting = f"-csearch_path={schema}"
    parts = urlsplit(cluster)
    query = parse_qsl(parts.query, keep_blank_values=True)

    # ONLY THE LAST `options` IS REAL, and merging into all of them corrupts the
    # DSN. libpq honours the last occurrence of a repeated URI parameter and
    # discards the rest, so a cluster carrying two of them — which happens
    # whenever a layer appends one naively instead of merging, as
    # `ephemeral_schema` does — has exactly one that matters. Merging into every
    # occurrence rewrote a parameter libpq was going to throw away, and the
    # result no longer had the cluster DSN as a prefix. Measured in the full
    # suite, where the pinned per-test store is already `?options=...` and the
    # fixture appends a second one.
    #
    # So the earlier duplicates are dropped rather than edited, which is what
    # libpq does with them anyway, and the setting is merged into the survivor.
    others = [(k, v) for k, v in query if k != "options"]
    existing = [v for k, v in query if k == "options"]
    effective = f"{existing[-1]} {setting}".strip() if existing else setting
    merged = others + [("options", effective)]
    # quote_via=quote, NOT the default quote_plus. libpq percent-decodes a URI
    # parameter but does NOT read "+" as a space, so the default encoding turns
    # a merged two-setting options string into one unparseable token -- which
    # surfaces as "no schema has been selected to create in" at the first CREATE
    # TABLE rather than as anything naming the DSN. Measured while writing this.
    return urlunsplit(parts._replace(query=urlencode(merged, quote_via=quote)))


#: The advisory-lock class for workspace provisioning. PostgreSQL keeps
#: TWO-argument advisory locks in a SEPARATE space from the one-argument
#: (bigint) form, so this cannot collide with the package's existing single-key
#: locks -- ``_db_foreign_keys._ADVISORY_LOCK_KEY`` and ``_store_tx``'s write
#: lock -- whatever integers those choose. That is why the two-argument form is
#: used here rather than a third bigint nobody can prove is unused.
_PROVISION_LOCK_CLASS = 0x5C1D0001


def _provision_lock_key(schema: str) -> int:
    """A stable signed int32 for ``schema``, for the lock's second argument.

    Derived from the digest the schema name already is, so the lock is keyed on
    the TENANT: two provisions of one identity serialise, two provisions of
    different identities do not touch each other. Shifted into signed range
    because ``pg_advisory_lock(int, int)`` takes int4, and a bare
    ``int(..., 16)`` overflows it for any digest starting above 0x7F.
    """
    digest = schema.removeprefix("ws_")
    return int(digest[:8], 16) - 0x80000000


def provision_workspace_store(*segments: object) -> str:
    """Create the store for a workspace identity. Idempotent; returns its DSN.

    The sanctioned creation path, and the ONLY one. It exists because
    :func:`resolve_workspace_store` deliberately refuses to create - so without
    this verb every new tenant hits a fail-closed raise with no way forward, and
    the pressure would be to soften the resolver instead. Separating them keeps
    "I could not find it" and "make me one" as different requests.

    IDEMPOTENT BY REGISTRATION, not by exception: an already-provisioned
    workspace returns its DSN unchanged rather than raising or recreating,
    because re-provisioning must never truncate a store that already holds cards.

    CONCURRENT PROVISIONS ARE SERIALISED BY AN ADVISORY LOCK, and the sentence
    that used to stand here was wrong in a way worth recording, because it was
    the contract callers were told to rely on. It read: "``ON CONFLICT DO
    NOTHING`` and ``IF NOT EXISTS`` carry that, so two concurrent provisions of
    the same workspace settle rather than racing into an error."

    They do not carry it. ``IF NOT EXISTS`` CHECKS THEN CREATES, and PostgreSQL
    does not make that pair atomic against a concurrent create: two sessions
    both find the object absent, both issue the CREATE, and the loser takes a
    unique violation on the system catalogue -- ``pg_namespace_nspname_index``
    for the schema, ``pg_type_typname_nsp_index`` for a table. MEASURED, not
    reasoned: eight threads provisioning one identity reproduce it in under
    five seconds, and CI hit the table half of it on 2026-08-31.

    So the lock is what carries it now. It is keyed on the schema digest, so
    two provisions of DIFFERENT tenants never contend -- this serialises one
    tenant's creation, not the cluster's.

    IT REGISTERS THE TENANT AND BUILDS THE SCHEMA, not one or the other, and that
    is a contract rather than a convenience: :func:`resolve_workspace_store` reads
    the REGISTRY, so a draft that created only the schema would return success and
    leave the very next resolve raising StoreNotProvisionedError. A provision that
    does not satisfy the resolver is not a provision - it is a rename of the
    problem.

    The card tables are built by :func:`scitex_cards._db.open_db`, which creates
    them on first open and no-ops on an existing store. This verb decides WHICH
    SCHEMA and delegates HOW, so the one place that knows how to build a schema
    stays the one place that builds it.
    """
    # VALIDATE BEFORE READING CONFIGURATION, and the order is load-bearing: a
    # malformed identity is the CALLER's fault whatever our deployment looks
    # like, so it must not be masked by a StoreUnavailableError just because the
    # cluster happens to be unset in the environment the caller is testing in.
    schema = _schema_for(segments)
    cluster = _cluster_dsn()
    dsn = _workspace_dsn(cluster, schema)

    from ._backend_connect import connect  # noqa: PLC0415 -- import cycle

    conn = connect(cluster, read_only=False, rows_by_name=True)
    try:
        # SESSION-LEVEL, NOT TRANSACTION-LEVEL, and that is the whole point.
        # This function provisions across TWO connections -- the cluster one
        # below, then `open_db(dsn)` -- and the table-creation race CI actually
        # hit lives in the SECOND. A `pg_advisory_xact_lock` would release at
        # the `conn.commit()` a few lines down, i.e. before the vulnerable half
        # ever runs, and would have looked like a fix while changing nothing.
        # The session lock is released when this connection closes, including
        # on an exception or a crashed process, so `finally: conn.close()` is
        # the whole cleanup story.
        #
        # LOCK ORDER, stated because a second lock is where deadlocks come
        # from: `open_db` -> `init_schema` may take the migration lock
        # (`_db_foreign_keys._ADVISORY_LOCK_KEY`). This path therefore always
        # takes provision-then-migration and never the reverse, and no other
        # caller takes the provision lock at all, so no cycle exists.
        conn.execute(
            "SELECT pg_advisory_lock(?, ?)",
            [_PROVISION_LOCK_CLASS, _provision_lock_key(schema)],
        )
        # `schema` is a digest -- 32 hex characters produced above, never the
        # caller's bytes -- so it cannot carry an identifier quote. Quoted anyway
        # so the safety survives a future change to the derivation.
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_REGISTRY_TABLE} ("
            f"  segments    text[]      PRIMARY KEY,"
            f"  schema_name text        NOT NULL UNIQUE,"
            f"  created_at  timestamptz NOT NULL DEFAULT now()"
            f")"
        )
        # segments is text[], so the ELEMENT BOUNDARIES are stored rather than a
        # joined string: ('alice-my','project') and ('alice','my-project') are
        # different keys to the PRIMARY KEY itself, not merely different digests.
        # ON CONFLICT NAMES ITS TARGET, and the bare form is a trap I walked
        # into and measured. `ON CONFLICT DO NOTHING` with no target absorbs
        # EVERY unique violation on this table -- including the schema_name one,
        # which fires when two DIFFERENT identities derive the same schema. The
        # insert then does nothing, no error is raised, and this function hands
        # the second tenant a handle to the FIRST tenant's schema: a silent
        # cross-tenant read, which is precisely the failure this module exists
        # to make unrepresentable. Naming `(segments)` absorbs only the
        # idempotent re-provision and lets a schema collision RAISE, so the
        # UNIQUE constraint becomes a real second line of defence behind the
        # digest rather than a decoration.
        conn.execute(
            f"INSERT INTO {_REGISTRY_TABLE} (segments, schema_name) "
            f"VALUES (?, ?) ON CONFLICT (segments) DO NOTHING",
            [list(str(segment) for segment in segments), schema],
        )
        conn.commit()

        # INSIDE THE LOCK, DELIBERATELY. This is the statement that failed on
        # CI -- `open_db` builds the card tables, and two workers reaching it
        # together is exactly the pg_type collision. Moving it out of the
        # `try` to keep the connection short-lived would restore the bug.
        from ._db import open_db  # noqa: PLC0415 -- import cycle

        open_db(dsn).close()
    finally:
        conn.close()
    return dsn


def resolve_workspace_store(*segments: object) -> str:
    """The canonical store for a workspace identity. RAISES rather than guessing.

    ``segments`` are the ordered components of the identity - for a
    two-dimensional tenancy, ``("user", owner, project)``. They are joined on a
    character the validator forbids inside a segment, so the join has one parse
    and ``("alice-my", "project")`` and ``("alice", "my-project")`` are different
    stores. A single-segment caller sees exactly the previous behaviour.

    NEVER CREATES. A resolver that creates on miss turns a typo into a new empty
    tenant, silently, and the caller cannot tell that from a workspace that
    genuinely existed. Creation is :func:`provision_workspace_store`, which a
    caller has to mean.

    Raises:
        InvalidWorkspaceIdentity: no segments, or any segment is not slug-shaped
            - including any location, any traversal attempt, and any non-string.
        StoreUnavailableError: the cluster is unconfigured. That is OUR deployment
            misconfigured, never the tenant's fault.
        StoreNotProvisionedError: the workspace has no store yet. Distinct from
            the above so a caller can render onboarding for one and an error for
            the other.
    """
    schema = _schema_for(segments)  # before _cluster_dsn(); see provision
    cluster = _cluster_dsn()

    from ._backend_connect import connect  # noqa: PLC0415 -- import cycle

    conn = connect(cluster, read_only=True, rows_by_name=True)
    try:
        # to_regclass rather than letting a missing table raise: an UndefinedTable
        # aborts the transaction, so the diagnostic a caller sees would be about
        # the aborted transaction rather than about the tenant not existing. A
        # cluster where nothing has ever been provisioned is the ORDINARY first
        # state, not an error.
        registry = conn.fetchone(
            "SELECT to_regclass(?) IS NOT NULL AS present", [_REGISTRY_TABLE]
        )
        row = None
        if registry["present"]:
            row = conn.fetchone(
                f"SELECT schema_name FROM {_REGISTRY_TABLE} WHERE segments = ?",
                [list(str(segment) for segment in segments)],
            )
    finally:
        conn.close()

    if row is None:
        # NOT-PROVISIONED, and on this path it is the ORDINARY case rather than
        # an exceptional one: a workspace that has never been set up is what
        # every new tenant looks like. The cluster being unset (above) stays the
        # parent type - that is OUR deployment misconfigured, not their tenancy
        # being new, and it must not render an onboarding page to everyone.
        raise StoreNotProvisionedError(
            f"workspace schema {schema} is not registered. REFUSING to continue: "
            f"an unprovisioned workspace reads back as an empty board, and a "
            f"caller cannot tell that from a tenant who has simply written "
            f"nothing yet. Provision the workspace rather than letting cards "
            f"invent one."
        )

    # The REGISTERED name, not the recomputed one. They agree today; reading the
    # row is what keeps them agreeing if the derivation is ever versioned.
    return _workspace_dsn(cluster, row["schema_name"])


# EOF
