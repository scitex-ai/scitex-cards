"""The suite must not carve its throwaway schemas on the live board's server.

These call ``_fleet_store_guard`` directly rather than importing ``conftest``,
because importing ``conftest`` OPENS A POSTGRESQL CLUSTER. A guard whose test
needs a database is a guard that stops being run.
"""

from _fleet_store_guard import CI_ENV, CLUSTER_ENV, fleet_store_declined, server_of

_PRIMARY = "postgresql://scitex-primary:55432/scitex"


#: One server, spelled two ways: different user, password and query string.
#: Comparing DSN STRINGS answers "different" here and carves on the primary,
#: which is the whole defect this guard exists for.
_DISGUISED = {
    CLUSTER_ENV: "postgresql://someone:secret@scitex-primary:55432/scitex",
}


def test_the_live_boards_server_is_declined_even_through_other_credentials():
    # Arrange
    env = dict(_DISGUISED)
    # Act
    reason = fleet_store_declined(env)
    # Assert
    assert reason is not None


def test_the_declining_reason_names_the_server_it_refused():
    # A reason that does not say WHICH server sends the reader hunting through
    # their environment for which of several DSNs was the problem.
    # Arrange
    env = dict(_DISGUISED)
    # Act
    reason = fleet_store_declined(env)
    # Assert
    assert "scitex-primary:55432/scitex" in (reason or "")


def test_the_ci_shape_is_untouched_because_no_board_is_configured():
    # pytest-matrix sets the cluster to its own service container and does NOT
    # set the board variable. If this ever returns a reason, CI stops using its
    # service container and starts trying to raise a throwaway one.
    # Arrange
    env = {
        CLUSTER_ENV: "postgresql://scitex_cards:scitex_cards@127.0.0.1:5432/scitex_cards",
        CI_ENV: "true",
    }
    # Act
    reason = fleet_store_declined(env)
    # Assert
    assert reason is None


def test_an_absent_cluster_variable_is_not_an_error():
    # Arrange
    env = {}
    # Act
    reason = fleet_store_declined(env)
    # Assert
    assert reason is None


def test_an_unparseable_dsn_declines_nothing_rather_than_raising():
    # A guard that raises at conftest import takes the whole session with it,
    # and it would do so for an environment problem it was only meant to warn
    # about. Refusing to act is the safe direction here BECAUSE the caller
    # still ends up on whatever writable_dsn() decides.
    # Arrange
    env = {CLUSTER_ENV: "not a dsn"}
    # Act
    reason = fleet_store_declined(env)
    # Assert
    assert reason is None


def test_the_default_port_is_filled_in_so_two_spellings_of_one_server_match():
    # Arrange
    explicit = "postgresql://host:5432/db"
    implied = "postgresql://host/db"
    # Act
    pair = (server_of(explicit), server_of(implied))
    # Assert
    assert pair == (("host", "5432", "db"), ("host", "5432", "db"))
