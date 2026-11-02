"""A trusted DM credential reaches the canonical ledger without widening readers."""

import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from scitex_dev.status import LEDGER_TABLE
from scitex_dev.store import Store, StoreProvisionError, StoreTarget, StoreTargetError, plugin_for
from scitex_dev.store.testing import ephemeral_cluster_dsn

from scitex_cards._dm.exchange import (
    accepted_status, failure_status, get_exchange, new_exchange, DmExchangeError,
    open_exchange_store, record_exchange, persist_dm,
)
from scitex_cards._dm._exchange_target import credential_target


@pytest.mark.parametrize('override', [
    'postgresql://writer@127.0.0.1:2/fixture',
    'postgresql://writer@127.0.0.1:1/other',
    'postgresql://writer@127.0.0.1:1/fixture?options=-csearch_path%3Dother',
    'postgresql://127.0.0.1:1/fixture',
])
def test_other_endpoints_or_implicit_roles_refuse_before_connect(override):
    # Arrange: neither localhost endpoint is a fixture server.
    canonical = StoreTarget.postgres('postgresql://reader@127.0.0.1:1/fixture',
                                     pkg='dev', name=LEDGER_TABLE)
    # Act
    try:
        credential_target(canonical, override)
        observed = None
    except StoreTargetError as error:
        observed = error
    # Assert
    assert isinstance(observed, StoreTargetError) and 'identical connection options' in str(observed)


@pytest.mark.parametrize('error,code,description', [
    (psycopg.errors.InsufficientPrivilege('private diagnostic'), 403, 'SQLSTATE 42501'),
    (psycopg.errors.InvalidPassword('private diagnostic'), 503, 'authentication refused'),
    (StoreProvisionError('authorized migration identity'), 503, 'schema is missing or unavailable'),
])
def test_typed_failure_never_guesses_auth_from_authorized(error, code, description):
    # Arrange
    exchange, _ = new_exchange()
    # Act
    status = failure_status(error, exchange, persistence_known_absent=True, stage='config')
    # Assert
    assert (status.code, description in status.message, 'private diagnostic' in status.message) == (code, True, False)


def _scoped_dsn(base, role, namespace):
    uri = urlsplit(base)
    query = dict(parse_qsl(uri.query))
    query['options'] = '-csearch_path=' + namespace
    return urlunsplit((uri.scheme, role+'@'+uri.netloc.rsplit('@',1)[-1],
                       uri.path, urlencode(query), ''))


@pytest.fixture(scope='module')
def private_ledger():
    """Independent disposable PostgreSQL; no fleet target or credentials are used."""
    with ephemeral_cluster_dsn() as base:
        namespace = 'ledger_' + uuid.uuid4().hex[:12]
        admin = _scoped_dsn(base, 'postgres', namespace)
        plugin = plugin_for(LEDGER_TABLE)
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace)))
            connection.execute('CREATE ROLE fixture_ledger_reader LOGIN')
            connection.execute('CREATE ROLE fixture_ledger_dm LOGIN')
        from scitex_cards._db import connect as connect_cards, init_schema
        cards_connection = connect_cards(admin)
        try:
            init_schema(cards_connection)
        finally:
            cards_connection.close()
        with Store(StoreTarget.postgres(admin, pkg='dev', name=LEDGER_TABLE),
                   plugin.schema, node='owned-fixture', writer_policy=plugin.writer_policy):
            pass
        with psycopg.connect(admin, autocommit=True) as connection:
            # Mirror the existing Cards-only read/append capability; keep the
            # four status-ledger tables excluded until the explicit grant test.
            card_tables = connection.execute(
                'SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'
            ).fetchall()
            for (name,) in card_tables:
                if not name.startswith('status_exchanges_'):
                    connection.execute(sql.SQL('GRANT SELECT ON {} TO fixture_ledger_reader,fixture_ledger_dm').format(sql.Identifier(name)))
            from scitex_cards._db_dm_schema import DM_TABLES
            for name in (*DM_TABLES,'notifications'):
                connection.execute(sql.SQL('GRANT INSERT ON {} TO fixture_ledger_dm').format(sql.Identifier(name)))
            connection.execute('GRANT USAGE ON SEQUENCE notifications_seq_seq TO fixture_ledger_dm')
            connection.execute('CREATE ROLE scitex_store_owner NOLOGIN')
            connection.execute('CREATE ROLE scitex_rw NOLOGIN')
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO fixture_ledger_reader,fixture_ledger_dm').format(sql.Identifier(namespace)))
            for name in ('rows','oplog','cursor','identity'):
                connection.execute(sql.SQL('ALTER TABLE {} OWNER TO scitex_store_owner').format(sql.Identifier('status_exchanges_'+name)))
        yield {'admin':admin,'reader':_scoped_dsn(base,'fixture_ledger_reader',namespace),
               'writer':_scoped_dsn(base,'fixture_ledger_dm',namespace)}


@pytest.fixture()
def routed_ledger(private_ledger):
    previous = os.environ.get('SCITEX_STORE_DSN')
    os.environ['SCITEX_STORE_DSN'] = private_ledger['reader']
    try:
        yield private_ledger
    finally:
        if previous is None:
            os.environ.pop('SCITEX_STORE_DSN', None)
        else:
            os.environ['SCITEX_STORE_DSN'] = previous


def _ledger_refusal():
    try:
        with open_exchange_store('owned-fixture-sender'):
            return None
    except StoreProvisionError as error:
        return error


def test_actual_managed_store_visibility_refusal_is_not_bad_password(routed_ledger):
    # Arrange
    exchange, _ = new_exchange()
    # Act
    caught = _ledger_refusal()
    status = failure_status(caught, exchange, stage='config')
    # Assert
    assert (status.code, 'schema is missing or unavailable' in status.message,
            'authentication refused' in status.message) == (503, True, False)


def _grant_fixture_ledger(routes):
    with psycopg.connect(routes['admin'],autocommit=True) as connection:
        for name in ('rows','oplog','cursor','identity'):
            connection.execute(sql.SQL('GRANT SELECT ON {} TO fixture_ledger_dm').format(sql.Identifier('status_exchanges_'+name)))
        connection.execute('GRANT INSERT,UPDATE ON status_exchanges_rows TO fixture_ledger_dm')
        connection.execute('GRANT INSERT ON status_exchanges_oplog TO fixture_ledger_dm')


def test_minimal_dedicated_grants_allow_ledger_roundtrip_keep_reader_closed(routed_ledger):
    # Arrange: only the same role capabilities proposed for the dev deployment.
    _grant_fixture_ledger(routed_ledger)
    exchange, opened = new_exchange()
    # Act
    with open_exchange_store('owned-fixture-sender',ledger_store=routed_ledger['writer']) as ledger:
        record_exchange(ledger,exchange_id=exchange,opened_at=opened,
                        sender='owned-fixture-sender',recipient='owned-fixture-recipient',
                        status=accepted_status(exchange,'owned-fixture-message'))
    found = get_exchange(exchange,sender='owned-fixture-sender',ledger_store=routed_ledger['writer'])
    with psycopg.connect(routed_ledger['writer']) as connection:
        forbidden = connection.execute("SELECT has_table_privilege(current_user,'status_exchanges_rows','DELETE'),has_table_privilege(current_user,'status_exchanges_cursor','UPDATE'),has_table_privilege(current_user,'status_exchanges_identity','INSERT')").fetchone()
    refused = _ledger_refusal()
    # Assert
    assert (found['status']['code'],found['initiator'],forbidden,isinstance(refused, StoreProvisionError)) == (202,'owned-fixture-sender',(False,False,False),True)


class _AuthenticatedPrincipal:
    is_authenticated = True

    def __init__(self, username):
        self.username = username

    def get_username(self):
        return self.username


@pytest.mark.parametrize('reader,expected', [('owned-fixture-sender',200), ('other-fixture-user',404)])
def test_django_status_view_uses_trusted_credential_and_sender_filter(routed_ledger, reader, expected):
    # Arrange
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','scitex_cards._django.settings')
    import django
    django.setup()
    from django.test import RequestFactory
    from scitex_cards._django.handlers.dm import dm_exchange_view
    _grant_fixture_ledger(routed_ledger)
    exchange,opened=new_exchange()
    with open_exchange_store('owned-fixture-sender',ledger_store=routed_ledger['writer']) as ledger:
        record_exchange(ledger,exchange_id=exchange,opened_at=opened,
                        sender='owned-fixture-sender',recipient='owned-fixture-recipient',
                        status=accepted_status(exchange,'owned-fixture-message'))
    request=RequestFactory().get('/dm/exchange/'+exchange,{'store':'postgresql://attacker@127.0.0.1:1/other'})
    # The authenticated principal is input from the outer host boundary,
    # not a claim that this direct view test exercises password authentication.
    request.user=_AuthenticatedPrincipal(reader)
    request.scitex_store=routed_ledger['writer']
    # Act
    response=dm_exchange_view(request,exchange)
    # Assert
    assert response.status_code==expected


def _wrapped_failure(inner, suppressed):
    try:
        try:
            raise inner
        except psycopg.Error as caught:
            if suppressed:
                raise StoreProvisionError('authorized migration identity') from None
            raise StoreProvisionError('authorized migration identity') from caught
    except StoreProvisionError as wrapper:
        return wrapper


@pytest.mark.parametrize('suppressed',[False,True])
@pytest.mark.parametrize('inner,expected,description',[
    (psycopg.errors.InsufficientPrivilege('fixture-private'),403,'SQLSTATE 42501'),
    (psycopg.errors.InvalidPassword('fixture-private'),503,'authentication refused'),
])
def test_native_sqlstate_wins_over_schema_wrapper_even_with_suppressed_context(inner,expected,description,suppressed):
    # Arrange
    exchange,_=new_exchange()
    # Act
    wrapped=_wrapped_failure(inner,suppressed)
    status=failure_status(wrapped,exchange,stage='config')
    # Assert
    assert (status.code,description in status.message,'fixture-private' in status.message)==(expected,True,False)


def test_actual_dm_persistence_notification_ledger_and_idempotent_retry(routed_ledger):
    # Arrange
    _grant_fixture_ledger(routed_ledger)
    request_id='req_'+uuid.uuid4().hex
    # Act
    try:
        first=persist_dm('owned-fixture-sender','owned-fixture-recipient','owned fixture only',
                         store=routed_ledger['writer'],ledger_store=routed_ledger['writer'],client_request_id=request_id)
    except DmExchangeError as error:
        raise RuntimeError('Owned PostgreSQL fixture refusal') from error.__context__
    repeated=persist_dm('owned-fixture-sender','owned-fixture-recipient','owned fixture only',
                        store=routed_ledger['writer'],ledger_store=routed_ledger['writer'],client_request_id=request_id)
    with psycopg.connect(routed_ledger['admin']) as connection:
        dm_count=connection.execute('SELECT count(*) FROM dm_messages WHERE client_request_id=%s',(request_id,)).fetchone()[0]
        notification_count=connection.execute('SELECT count(*) FROM notifications WHERE exchange_id=%s',(first['exchange_id'],)).fetchone()[0]
        ledger_count=connection.execute('SELECT count(*) FROM status_exchanges_rows WHERE exchange_id=%s',(first['exchange_id'],)).fetchone()[0]
    # Assert
    assert (first['status']['code'],repeated['status']['code'],first['id']==repeated['id'],first['exchange_id']==repeated['exchange_id'],dm_count,notification_count,ledger_count)==(202,202,True,True,1,1,1)


def test_ordinary_explicit_data_store_does_not_retarget_canonical_ledger(
    new_store, env
):
    # Arrange: two genuine owned schemas; only the canonical one owns the ledger.
    canonical = new_store()
    data = new_store()
    env.set("SCITEX_STORE_DSN", canonical)
    request_id = "req_" + uuid.uuid4().hex
    # Act
    from scitex_cards import dm_send

    record = dm_send(
        "owned-fixture-recipient",
        "owned fixture",
        sender="owned-fixture-sender",
        store=data,
        client_request_id=request_id,
    )
    found = get_exchange(record["exchange_id"], sender="owned-fixture-sender")
    with psycopg.connect(data) as connection:
        dm_count = connection.execute(
            "SELECT count(*) FROM dm_messages WHERE client_request_id=%s", (request_id,)
        ).fetchone()[0]
        data_has_ledger = connection.execute(
            "SELECT to_regclass('status_exchanges_rows') IS NOT NULL"
        ).fetchone()[0]
    # Assert
    assert (
        record["status"]["code"],
        found["status"]["code"],
        dm_count,
        data_has_ledger,
    ) == (202, 202, 1, False)


def test_django_post_uses_server_ledger_hint_ignores_body_hint(routed_ledger):
    # Arrange: valid server role and invalid body credential point at different targets.
    import json

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")
    import django

    django.setup()
    from django.test import RequestFactory
    from scitex_cards._django.handlers.dm import dm_thread_view

    _grant_fixture_ledger(routed_ledger)
    request = RequestFactory().post(
        "/dm/owned-fixture-recipient",
        data=json.dumps(
            {
                "body": "owned fixture only",
                "client_request_id": "req_" + uuid.uuid4().hex,
                "ledger_store": "postgresql://caller@127.0.0.1:1/other",
            }
        ),
        content_type="application/json",
    )
    request.user = _AuthenticatedPrincipal("owned-fixture-sender")
    request.scitex_store = routed_ledger["writer"]
    # Act
    response = dm_thread_view(request, "owned-fixture-recipient")
    payload = json.loads(response.content)
    # Assert
    assert (response.status_code, payload.get("exchange", {}).get("status", {}).get("code")) == (202, 202)
