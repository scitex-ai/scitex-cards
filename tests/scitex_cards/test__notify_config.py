#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the C3 notify config + pure recipient resolver.

Real round-trips: real users via ``register_user`` in a ``tmp_path`` store,
real cards as plain dicts, real ``notify.json`` sidecars on disk. No mocks
(STX-NM / PA-306). AAA pattern.

Coverage mirrors the C3 card's test checklist:

* default resolution per event type (built-in rules),
* role → user-id resolution via the registry; unresolved name → raw string,
* per-user ``mute`` removes a recipient; ``watch`` adds a non-default member,
* per-card ``events`` override / ``add`` force-include / ``mute``
  force-exclude (card beats user/global),
* precedence end-to-end (global < user < card),
* ``notify.json`` sidecar override honored; malformed sidecar fails loud;
  absent sidecar → built-in defaults.
"""

from __future__ import annotations

import pytest

from scitex_cards._events import Event, EventType
from scitex_cards._notify import (
    DEFAULT_NOTIFY_RULES,
    NOTIFY_SIDECAR_NAME,
    NotifyConfig,
    NotifyConfigError,
    card_role_members,
    load_notify_config,
    resolve_recipients,
)
from scitex_cards._users import register_user, set_notify


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_sidecar(tmp_path, payload):
    """Write a notify.json sidecar next to the (tmp) tasks store."""
    import json

    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return sidecar


# --------------------------------------------------------------------------- #
# load_notify_config: zero-config defaults + sidecar                          #
# --------------------------------------------------------------------------- #
#: WHY the two `no_sidecar` tests below are split but share this rationale:
#: zero-config must work, and "works" is two claims — the loader returns a real
#: NotifyConfig (not None, not a bare dict) AND the rules it carries are the
#: built-in SSOT returned UNCHANGED. A loader that returns the right type with
#: silently-mutated rules is the failure a single first-assert would hide.
def test_default_config_is_a_notify_config_instance(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    cfg = load_notify_config(store=store)
    # Assert
    assert isinstance(cfg, NotifyConfig)


def test_default_config_is_built_in_rules_when_no_sidecar(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    cfg = load_notify_config(store=store)
    # Assert — the built-in SSOT is returned unchanged (zero-config works).
    assert cfg.rules == DEFAULT_NOTIFY_RULES


def test_default_rules_cover_every_event_type():
    # Arrange — complete-over-taxonomy invariant: a reader sees the full policy.
    from scitex_cards._events import EVENT_TYPES

    expected = set(EVENT_TYPES)
    # Act
    covered = set(DEFAULT_NOTIFY_RULES)
    # Assert
    assert covered == expected


#: WHY the two `sidecar_overrides` tests below are split but share this
#: rationale: an override is only correct if it is SCOPED. The sidecar must
#: replace the role list for the event it names AND leave every other event on
#: its built-in default — a sidecar that quietly resets the untouched events is
#: a config wipe wearing an override's clothes.
def test_sidecar_overrides_the_named_event_rule(tmp_path):
    # Arrange — sidecar replaces the role list for `commented`.
    _write_sidecar(tmp_path, {"rules": {"commented": ["subscribers"]}})
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert
    assert cfg.roles_for("commented") == ["subscribers"]


def test_sidecar_leaves_other_event_rules_at_their_defaults(tmp_path):
    # Arrange — the same sidecar, which says nothing about `merged`.
    _write_sidecar(tmp_path, {"rules": {"commented": ["subscribers"]}})
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert — an unnamed event keeps the built-in default.
    assert cfg.roles_for("merged") == DEFAULT_NOTIFY_RULES["merged"]


def test_sidecar_bare_mapping_shape_is_accepted(tmp_path):
    # Arrange — a bare top-level mapping (no `rules:` key) is the rules map.
    _write_sidecar(tmp_path, {"pulled": ["owner"]})
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert
    assert cfg.roles_for("pulled") == ["owner"]


def test_malformed_sidecar_unknown_event_fails_loud(tmp_path):
    # Arrange
    _write_sidecar(tmp_path, {"rules": {"not_an_event": ["owner"]}})
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_unknown_role_fails_loud(tmp_path):
    # Arrange
    _write_sidecar(tmp_path, {"rules": {"commented": ["wizard"]}})
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_non_mapping_top_level_fails_loud(tmp_path):
    # Arrange — valid JSON, but a list at the top level (not a mapping).
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text('["just", "a", "list"]\n', encoding="utf-8")
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        load_notify_config(store=_store(tmp_path))


def test_malformed_sidecar_bad_json_fails_loud(tmp_path):
    # Arrange — syntactically invalid JSON.
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text('{"commented": [owner\n', encoding="utf-8")
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        load_notify_config(store=_store(tmp_path))


def test_empty_sidecar_yields_built_in_defaults(tmp_path):
    # Arrange
    sidecar = tmp_path / NOTIFY_SIDECAR_NAME
    sidecar.write_text("", encoding="utf-8")
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert
    assert cfg.rules == DEFAULT_NOTIFY_RULES


def test_a_lone_pre_json_sidecar_is_not_read(tmp_path):
    """A pre-JSON notify sidecar has no import path any more — it is ignored."""
    # Arrange — only a stray pre-JSON sidecar exists; no notify.json.
    (tmp_path / "notify.pre-json").write_text(
        '{"rules": {"commented": ["subscribers"]}}\n',
        encoding="utf-8",
    )
    # Act
    cfg = load_notify_config(store=_store(tmp_path))
    # Assert — nothing read from it; built-ins apply, file left untouched.
    assert cfg.rules == DEFAULT_NOTIFY_RULES
    assert not (tmp_path / "notify.json").exists()
    assert (tmp_path / "notify.pre-json").exists()


# --------------------------------------------------------------------------- #
# card_role_members: role -> user-id resolution + raw-name fallback           #
# --------------------------------------------------------------------------- #
#: WHY the three `role_members_resolve` tests below are split but share this
#: rationale: `card_role_members` returns the WHOLE role map in one call, and
#: each role is a separate claim — a registered owner resolves to an id, a
#: registered collaborator resolves to an id, and a role nobody fills resolves
#: to an EMPTY set rather than being absent or leaking another role's members.
#: The empty-subscribers claim is the one a first-assert failure would hide,
#: and it is the one that silently over-notifies when it breaks.
@pytest.fixture()
def role_members_with_owner_and_collaborator(tmp_path):
    """Resolve one card carrying a registered owner AND a registered collaborator."""
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    card = {"id": "c1", "agent": "alice", "collaborators": ["bob"]}
    return {
        "members": card_role_members(card, store=store),
        "owner": owner,
        "collab": collab,
    }


def test_card_role_members_resolves_the_owner_to_an_id(
    role_members_with_owner_and_collaborator,
):
    # Arrange
    scenario = role_members_with_owner_and_collaborator
    # Act
    members = scenario["members"]
    # Assert
    assert members["owner"] == {scenario["owner"].id}


def test_card_role_members_resolves_collaborators_to_ids(
    role_members_with_owner_and_collaborator,
):
    # Arrange
    scenario = role_members_with_owner_and_collaborator
    # Act
    members = scenario["members"]
    # Assert
    assert members["collaborators"] == {scenario["collab"].id}


def test_card_role_members_leaves_an_unfilled_role_empty(
    role_members_with_owner_and_collaborator,
):
    # Arrange
    scenario = role_members_with_owner_and_collaborator
    # Act
    members = scenario["members"]
    # Assert — nobody subscribed, so the role is empty rather than absent.
    assert members["subscribers"] == set()


#: WHY the two `legacy_assignee` tests below are split but share this
#: rationale: a card with no `agent` field must still resolve BOTH the `owner`
#: role (via the legacy fallback) and the `assignee` role to that same user.
#: Fixing one without the other is what leaves legacy cards half-notified.
@pytest.fixture()
def role_members_from_legacy_assignee(tmp_path):
    """No `agent` field → owner role resolves from `assignee` (legacy)."""
    store = _store(tmp_path)
    user = register_user(kind="agent", names=["carol"], store=store)
    card = {"id": "c1", "assignee": "carol"}
    return {"members": card_role_members(card, store=store), "user": user}


def test_card_role_members_owner_falls_back_to_assignee(
    role_members_from_legacy_assignee,
):
    # Arrange
    scenario = role_members_from_legacy_assignee
    # Act
    members = scenario["members"]
    # Assert
    assert members["owner"] == {scenario["user"].id}


def test_card_role_members_also_fills_the_assignee_role(
    role_members_from_legacy_assignee,
):
    # Arrange
    scenario = role_members_from_legacy_assignee
    # Act
    members = scenario["members"]
    # Assert
    assert members["assignee"] == {scenario["user"].id}


def test_unresolved_name_falls_back_to_raw_string(tmp_path):
    # Arrange — `dave` is NOT registered → the raw name string is the id.
    store = _store(tmp_path)
    card = {"id": "c1", "agent": "dave"}
    # Act
    members = card_role_members(card, store=store)
    # Assert
    assert members["owner"] == {"dave"}


# --------------------------------------------------------------------------- #
# resolve_recipients: default resolution per event type                       #
# --------------------------------------------------------------------------- #
def test_commented_resolves_owner_collaborators_subscribers(tmp_path):
    # Arrange
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "collaborators": ["bob"],
        "subscribers": ["eve"],
    }
    # Act
    got = resolve_recipients(
        Event(type=EventType.COMMENTED, card_id="c1"), card, store=store
    )
    # Assert
    assert got == {owner.id, collab.id, sub.id}


def test_pulled_resolves_to_empty_by_default(tmp_path):
    # Arrange
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice", "subscribers": ["alice"]}
    # Act
    got = resolve_recipients(
        Event(type=EventType.PULLED, card_id="c1"), card, store=store
    )
    # Assert — `pulled` is default-quiet (empty role list).
    assert got == set()


def test_resolver_accepts_event_dict(tmp_path):
    # Arrange — the resolver accepts an Event OR a plain wire dict.
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice"}
    # Act
    got = resolve_recipients({"type": "reassigned"}, card, store=store)
    # Assert
    assert got == {owner.id}


def test_resolver_unknown_event_type_fails_loud(tmp_path):
    # Arrange
    store = _store(tmp_path)
    card = {"id": "c1", "agent": "alice"}
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        resolve_recipients({"type": "teleported"}, card, store=store)


# --------------------------------------------------------------------------- #
# per-user mute / watch (layer 2)                                             #
# --------------------------------------------------------------------------- #
#: WHY the two `per_user_mute` tests below are split but share this rationale:
#: a mute is a TARGETED removal, so it carries two claims that fail in opposite
#: directions — the muter must be dropped, and everyone else must survive. A
#: mute that drops the whole recipient set passes the "she was removed" half
#: while silencing the card entirely.
@pytest.fixture()
def commented_recipients_with_muted_subscriber(tmp_path):
    """eve mutes `commented` → dropped even though she's a subscriber."""
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    set_notify(sub.id, {"mute": ["commented"]}, store=store)
    card = {"id": "c1", "agent": "alice", "subscribers": ["eve"]}
    return {
        "got": resolve_recipients(Event(type=EventType.COMMENTED), card, store=store),
        "owner": owner,
        "sub": sub,
    }


def test_per_user_mute_keeps_the_unmuted_owner(
    commented_recipients_with_muted_subscriber,
):
    # Arrange
    scenario = commented_recipients_with_muted_subscriber
    # Act
    got = scenario["got"]
    # Assert — the mute is targeted, not a blanket silence.
    assert scenario["owner"].id in got


def test_per_user_mute_removes_recipient(
    commented_recipients_with_muted_subscriber,
):
    # Arrange
    scenario = commented_recipients_with_muted_subscriber
    # Act
    got = scenario["got"]
    # Assert
    assert scenario["sub"].id not in got


def test_per_user_watch_adds_member_not_in_default_roles(tmp_path):
    # Arrange — `completed` default = [owner, subscribers]; bob is a
    # COLLABORATOR (not in the default roles) but watches `completed` → opted
    # in because he's a member. (Uses `completed` not `merged`: C4 made
    # `merged` default-quiet → [], so it no longer carries a base `owner`
    # recipient to assert on.)
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    set_notify(collab.id, {"watch": ["completed"]}, store=store)
    card = {"id": "c1", "agent": "alice", "collaborators": ["bob"]}
    # Act
    got = resolve_recipients(Event(type=EventType.COMPLETED), card, store=store)
    # Assert
    assert got == {owner.id, collab.id}


#: WHY the two `watch_non_member` tests below are split but share this
#: rationale: a `watch` is an opt-in that only applies to someone already ON
#: the card. mallory watches `completed` but is not a member, so the recipient
#: set must be EXACTLY the owner — asserted both as the whole set and as her
#: specific absence, because "the set is right" and "she in particular did not
#: leak in" are the two ways this rule is read.
@pytest.fixture()
def completed_recipients_with_watching_outsider(tmp_path):
    """mallory watches `completed` but is NOT on the card → never added.

    (`completed`, not `merged`: see the note above — `merged` is now [].)
    """
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    outsider = register_user(kind="human", names=["mallory"], store=store)
    set_notify(outsider.id, {"watch": ["completed"]}, store=store)
    card = {"id": "c1", "agent": "alice"}
    return {
        "got": resolve_recipients(Event(type=EventType.COMPLETED), card, store=store),
        "owner": owner,
        "outsider": outsider,
    }


def test_per_user_watch_resolves_to_the_owner_alone(
    completed_recipients_with_watching_outsider,
):
    # Arrange
    scenario = completed_recipients_with_watching_outsider
    # Act
    got = scenario["got"]
    # Assert
    assert got == {scenario["owner"].id}


def test_per_user_watch_does_not_add_non_member(
    completed_recipients_with_watching_outsider,
):
    # Arrange
    scenario = completed_recipients_with_watching_outsider
    # Act
    got = scenario["got"]
    # Assert
    assert scenario["outsider"].id not in got


def test_user_mute_beats_role_membership_but_card_add_can_reinclude(tmp_path):
    # Arrange — mute drops a recipient; a per-card `add` re-includes them
    # (card wins).
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    set_notify(owner.id, {"mute": ["completed"]}, store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"add": {"completed": ["alice"]}},
    }
    # Act
    got = resolve_recipients(Event(type=EventType.COMPLETED), card, store=store)
    # Assert
    assert owner.id in got


# --------------------------------------------------------------------------- #
# per-card overrides (layer 3)                                                #
# --------------------------------------------------------------------------- #
#: WHY the two `per_card_events_override` tests below are split but share this
#: rationale: narrowing a card's roles is both an INCLUSION and an EXCLUSION —
#: the surviving role must still resolve, and the dropped role's member must
#: actually be gone. An override that resolves to the right set by accident
#: (say, an empty set) fails the first claim; one that never applied fails the
#: second.
@pytest.fixture()
def commented_recipients_narrowed_to_subscribers(tmp_path):
    """Default `commented` = owner+collab+subscribers; this card narrows it."""
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "subscribers": ["eve"],
        "notify": {"events": {"commented": ["subscribers"]}},
    }
    return {
        "got": resolve_recipients(Event(type=EventType.COMMENTED), card, store=store),
        "owner": owner,
        "sub": sub,
    }


def test_per_card_events_override_changes_roles(
    commented_recipients_narrowed_to_subscribers,
):
    # Arrange
    scenario = commented_recipients_narrowed_to_subscribers
    # Act
    got = scenario["got"]
    # Assert
    assert got == {scenario["sub"].id}


def test_per_card_events_override_drops_the_removed_role(
    commented_recipients_narrowed_to_subscribers,
):
    # Arrange
    scenario = commented_recipients_narrowed_to_subscribers
    # Act
    got = scenario["got"]
    # Assert — the owner role was narrowed away, so its member is gone.
    assert scenario["owner"].id not in got


def test_merged_is_default_quiet(tmp_path):
    # Arrange — C4 decision: `merged` defaults to [] (a PR-merge-close also
    # fires `completed`, the canonical done-notice — defaulting `merged` to
    # recipients too would double-notify). With zero per-card opt-in, a
    # `merged` event resolves to NOBODY even for the owner.
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice"}
    # Act
    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)
    # Assert
    assert got == set()


def test_per_card_add_force_includes(tmp_path):
    # Arrange — frank is not in any role but is force-added for `completed`.
    # (Uses `completed` not `merged`: `merged` is now default-quiet [], so
    # there is no base `owner` recipient to assert alongside the force-add.)
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    extra = register_user(kind="human", names=["frank"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"add": {"completed": ["frank"]}},
    }
    # Act
    got = resolve_recipients(Event(type=EventType.COMPLETED), card, store=store)
    # Assert
    assert got == {owner.id, extra.id}


def test_per_card_add_opts_a_quiet_event_back_in(tmp_path):
    # Arrange — the opt-in path for the now-quiet `merged`: a card can still
    # force a merge ping via its per-card `notify.add`, even though the
    # default is [].
    store = _store(tmp_path)
    extra = register_user(kind="human", names=["frank"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"add": {"merged": ["frank"]}},
    }
    # Act
    got = resolve_recipients(Event(type=EventType.MERGED), card, store=store)
    # Assert
    assert got == {extra.id}


#: WHY the two `per_card_mute_force_excludes` tests below are split but share
#: this rationale: the card layer beating the user layer is a two-sided claim.
#: eve WATCHES commented (an opt-in) but the card mutes her, so she must be
#: gone — while the owner, who neither opted in nor was muted, must remain.
#: A card mute that silences everyone satisfies the exclusion half alone.
@pytest.fixture()
def commented_recipients_with_card_muted_watcher(tmp_path):
    """eve WATCHES commented (opt-in) but the card mutes her → card wins."""
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    sub = register_user(kind="human", names=["eve"], store=store)
    set_notify(sub.id, {"watch": ["commented"]}, store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "subscribers": ["eve"],
        "notify": {"mute": ["eve"]},
    }
    return {
        "got": resolve_recipients(Event(type=EventType.COMMENTED), card, store=store),
        "owner": owner,
        "sub": sub,
    }


def test_per_card_mute_keeps_the_unmuted_owner(
    commented_recipients_with_card_muted_watcher,
):
    # Arrange
    scenario = commented_recipients_with_card_muted_watcher
    # Act
    got = scenario["got"]
    # Assert
    assert scenario["owner"].id in got


def test_per_card_mute_force_excludes_beating_user_and_global(
    commented_recipients_with_card_muted_watcher,
):
    # Arrange
    scenario = commented_recipients_with_card_muted_watcher
    # Act
    got = scenario["got"]
    # Assert — the card's mute beats her own `watch` opt-in.
    assert scenario["sub"].id not in got


#: WHY the two `per_card_mute_beats_add` tests below are split but share this
#: rationale: mute is applied LAST, so it removes someone `add` just included.
#: That is two claims — frank, added then muted, is gone; and the owner, whom
#: neither clause names, still survives the add+mute round trip. (Uses
#: `completed` not `merged`: `merged` is now default-quiet [], so it has no
#: base `owner` recipient to assert survives.)
@pytest.fixture()
def completed_recipients_with_card_add_then_mute(tmp_path):
    """A card that force-adds frank and then mutes him in the same clause."""
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    frank = register_user(kind="human", names=["frank"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {
            "add": {"completed": ["frank"]},
            "mute": ["frank"],
        },
    }
    return {
        "got": resolve_recipients(Event(type=EventType.COMPLETED), card, store=store),
        "owner": owner,
        "frank": frank,
    }


def test_per_card_mute_beats_per_card_add(
    completed_recipients_with_card_add_then_mute,
):
    # Arrange
    scenario = completed_recipients_with_card_add_then_mute
    # Act
    got = scenario["got"]
    # Assert
    assert got == {scenario["owner"].id}


def test_per_card_add_then_mute_leaves_the_added_user_out(
    completed_recipients_with_card_add_then_mute,
):
    # Arrange
    scenario = completed_recipients_with_card_add_then_mute
    # Act
    got = scenario["got"]
    # Assert — mute runs last, so the just-added frank is removed again.
    assert scenario["frank"].id not in got


def test_malformed_card_notify_fails_loud(tmp_path):
    # Arrange
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice", "notify": ["not", "a", "mapping"]}
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        resolve_recipients(Event(type=EventType.COMMENTED), card, store=store)


def test_malformed_card_events_unknown_role_fails_loud(tmp_path):
    # Arrange
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    card = {
        "id": "c1",
        "agent": "alice",
        "notify": {"events": {"commented": ["sorcerer"]}},
    }
    # Act
    ctx = pytest.raises(NotifyConfigError)
    # Assert
    with ctx:
        resolve_recipients(Event(type=EventType.COMMENTED), card, store=store)


# --------------------------------------------------------------------------- #
# precedence end-to-end: global < user < card                                 #
# --------------------------------------------------------------------------- #
#: WHY the three `precedence` tests below are split but share this rationale:
#: ONE scenario exercises all three layers + the sidecar at once. Walk-through:
#:
#:   global(sidecar) merged roles = owner+collaborators = {alice, bob}
#:   user mute: bob drops        -> {alice}
#:   user watch: eve is a member -> {alice, eve}
#:   card add: frank             -> {alice, eve, frank}
#:   card mute: eve              -> {alice, frank}
#:
#: The final set and the two specific exclusions are separate claims: bob was
#: dropped by the USER layer and eve by the CARD layer beating her own user
#: opt-in. Asserting only the final set would still pass if the two exclusions
#: happened for the wrong reason, and asserting only the exclusions would miss
#: frank's inclusion — so each is pinned on its own.
@pytest.fixture()
def merged_recipients_across_all_precedence_layers(tmp_path):
    """Global sidecar + per-user mute/watch + per-card add/mute, in one card."""
    store = _store(tmp_path)
    # Sidecar (global): `merged` notifies owner + collaborators (override the
    # built-in owner+subscribers).
    _write_sidecar(tmp_path, {"rules": {"merged": ["owner", "collaborators"]}})

    owner = register_user(kind="agent", names=["alice"], store=store)
    collab = register_user(kind="agent", names=["bob"], store=store)
    watcher = register_user(kind="human", names=["eve"], store=store)
    added = register_user(kind="human", names=["frank"], store=store)

    # User layer: bob mutes merged (opt-out); eve watches merged (opt-in,
    # she's a subscriber so a member).
    set_notify(collab.id, {"mute": ["merged"]}, store=store)
    set_notify(watcher.id, {"watch": ["merged"]}, store=store)

    card = {
        "id": "c1",
        "agent": "alice",
        "collaborators": ["bob"],
        "subscribers": ["eve"],
        # Card layer: add frank; mute eve (card beats her watch).
        "notify": {"add": {"merged": ["frank"]}, "mute": ["eve"]},
    }
    return {
        "got": resolve_recipients(Event(type=EventType.MERGED), card, store=store),
        "owner": owner,
        "collab": collab,
        "watcher": watcher,
        "added": added,
    }


def test_precedence_global_then_user_then_card(
    merged_recipients_across_all_precedence_layers,
):
    # Arrange
    scenario = merged_recipients_across_all_precedence_layers
    # Act
    got = scenario["got"]
    # Assert — the owner survives every layer and frank is force-added.
    assert got == {scenario["owner"].id, scenario["added"].id}


def test_precedence_user_mute_drops_the_collaborator(
    merged_recipients_across_all_precedence_layers,
):
    # Arrange
    scenario = merged_recipients_across_all_precedence_layers
    # Act
    got = scenario["got"]
    # Assert — bob opted out at the USER layer.
    assert scenario["collab"].id not in got


def test_precedence_card_mute_beats_the_user_watch(
    merged_recipients_across_all_precedence_layers,
):
    # Arrange
    scenario = merged_recipients_across_all_precedence_layers
    # Act
    got = scenario["got"]
    # Assert — eve opted IN at the user layer; the card layer still wins.
    assert scenario["watcher"].id not in got


def test_shared_config_is_reused_across_resolves(tmp_path):
    # Arrange — passing a pre-loaded config should give the same answer as
    # auto-load.
    store = _store(tmp_path)
    owner = register_user(kind="agent", names=["alice"], store=store)
    card = {"id": "c1", "agent": "alice"}
    cfg = load_notify_config(store=store)
    # Act
    auto = resolve_recipients(Event(type=EventType.REASSIGNED), card, store=store)
    shared = resolve_recipients(
        Event(type=EventType.REASSIGNED), card, store=store, config=cfg
    )
    # Assert
    assert auto == shared == {owner.id}


# EOF
