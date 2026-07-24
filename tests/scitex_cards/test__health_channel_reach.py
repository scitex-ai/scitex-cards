#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``channel_reaches_session`` health check.

Regression cover for the 2026-07-24 fleet-wide silent-deafness outage: the
scitex-todo -> scitex-cards rename re-registered the MCP server as
``scitex-cards`` while agent launch lines still allowlisted the pre-rename
``scitex-todo``. Every channel push was discarded on arrival, and because the
drain marks records seen regardless, card events and DMs were consumed and lost.
``channel_capable`` and ``channel_drain`` were GREEN throughout.

No mocks — the decision half is pure, so the outage is reproduced by passing the
two real name sets (STX-NM / PA-306).
"""

from __future__ import annotations

from scitex_cards._health_channel_reach import (
    CHANNEL_FLAG,
    allowlisted_channel_servers,
    check_channel_reaches_session,
    evaluate_reachability,
    registered_server_names,
)


class TestAllowlistParsing:
    """Both launch-flag spellings must parse; a launcher may emit either."""

    def test_split_form_is_parsed(self):
        # Arrange
        argv = ["claude", CHANNEL_FLAG, "server:scitex-cards"]
        # Act
        result = allowlisted_channel_servers(argv)
        # Assert
        assert result == {"scitex-cards"}

    def test_joined_form_is_parsed(self):
        # Arrange
        argv = ["claude", f"{CHANNEL_FLAG}=server:sac"]
        # Act
        result = allowlisted_channel_servers(argv)
        # Assert
        assert result == {"sac"}

    def test_multiple_servers_accumulate(self):
        # Arrange — the real launch line carries one flag per server.
        argv = [
            "claude",
            CHANNEL_FLAG,
            "server:scitex-todo",
            CHANNEL_FLAG,
            "server:sac",
            CHANNEL_FLAG,
            "server:claude-code-telegrammer",
        ]
        # Act
        result = allowlisted_channel_servers(argv)
        # Assert
        assert result == {"scitex-todo", "sac", "claude-code-telegrammer"}

    def test_non_server_and_empty_values_are_ignored(self):
        # Arrange — a bare `server:` appears in real launch lines.
        argv = ["claude", CHANNEL_FLAG, "server:", CHANNEL_FLAG, "tool:something"]
        # Act
        result = allowlisted_channel_servers(argv)
        # Assert
        assert result == set()

    def test_absent_flag_yields_nothing(self):
        # Arrange
        argv = ["claude", "--model", "opus"]
        # Act
        result = allowlisted_channel_servers(argv)
        # Assert
        assert result == set()


class TestRegisteredServerNames:
    """Our server is identified by its COMMAND, never by assuming its name —
    the name is the thing under test."""

    def test_our_server_is_found_under_its_registered_key(self):
        # Arrange
        blobs = [
            {
                "mcpServers": {
                    "scitex-cards": {
                        "command": "/opt/venv/bin/scitex-cards",
                        "args": ["mcp", "start"],
                    }
                }
            }
        ]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == {"scitex-cards"}

    def test_foreign_servers_are_not_claimed(self):
        # Arrange
        blobs = [
            {
                "mcpServers": {
                    "claude-code-telegrammer": {"command": "cct", "args": ["serve"]},
                    "scitex-agent-container": {"command": "sac", "args": ["mcp"]},
                }
            }
        ]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == set()

    def test_pre_rename_shim_still_counts_as_ours(self):
        # Arrange — during a migration the old shim is still us; a check that
        # only knew the new name would be blind on the agents left behind.
        blobs = [
            {"mcpServers": {"scitex-todo": {"command": "scitex-todo", "args": ["mcp"]}}}
        ]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == {"scitex-todo"}

    def test_a_flag_value_naming_us_is_not_our_server(self):
        """THE false pass, caught live on 2026-07-24 before this shipped.

        sac's channel entry runs ``sac mcp channel --name scitex-cards``. A
        substring match over the command line claims that entry as OURS, finds
        it allowlisted (``sac`` is), and reports the channel healthy — masking
        the very outage this module exists to detect. Our name appearing as a
        FLAG VALUE names somebody else's server, never the program being run.
        """
        # Arrange
        blobs = [
            {
                "mcpServers": {
                    "sac": {
                        "command": "/bin/sh",
                        "args": [
                            "-c",
                            'exec sac "$@"',
                            "sac",
                            "mcp",
                            "channel",
                            "--name",
                            "scitex-cards",
                            "--turn-url",
                            "http://127.0.0.1:19011/v1/turn",
                        ],
                    }
                }
            }
        ]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == set()

    def test_module_form_counts_as_ours(self):
        # Arrange — `python -m scitex_cards...` is us even though the command
        # basename is the interpreter.
        blobs = [
            {
                "mcpServers": {
                    "scitex-cards": {
                        "command": "/usr/bin/python3",
                        "args": ["-m", "scitex_cards._mcp_server"],
                    }
                }
            }
        ]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == {"scitex-cards"}

    def test_malformed_config_does_not_raise(self):
        # Arrange — a health check must survive a config it cannot read.
        blobs = [{"mcpServers": "not-a-dict"}, {}, {"mcpServers": {"x": "nope"}}]
        # Act
        result = registered_server_names(blobs)
        # Assert
        assert result == set()


class TestReachabilityDecision:
    """The decision half — this is where the outage is reproduced."""

    def test_the_20260724_rename_outage_is_reported_not_ok(self):
        # Arrange — verbatim shape of the live incident: registered under the NEW
        # name, allowlisted under the OLD one.
        registered = {"scitex-cards"}
        allowed = {"scitex-todo", "sac", "claude-code-telegrammer"}
        # Act
        result = evaluate_reachability(allowed, registered)
        # Assert — and the hint must name the exact flag to add, or the reader
        # is told there is a problem and not what to do about it.
        assert result["ok"] is False
        assert "DISCARDED" in result["detail"]
        assert f"{CHANNEL_FLAG} server:scitex-cards" in result["hint"]
        assert "RESTART" in result["hint"]

    def test_matching_name_is_ok(self):
        # Arrange
        # Act
        result = evaluate_reachability({"scitex-cards", "sac"}, {"scitex-cards"})
        # Assert
        assert result["ok"] is True
        assert result["hint"] is None

    def test_transitional_both_names_allowlisted_is_ok(self):
        # Arrange — during the migration both names are allowlisted.
        # Act
        result = evaluate_reachability(
            {"scitex-todo", "scitex-cards"}, {"scitex-cards"}
        )
        # Assert
        assert result["ok"] is True

    def test_empty_allowlist_is_not_ok_when_we_are_registered(self):
        # Arrange — no channel flag at all means nothing we push is ever surfaced.
        # Act
        result = evaluate_reachability(set(), {"scitex-cards"})
        # Assert
        assert result["ok"] is False
        assert "no server at all" in result["detail"]

    def test_not_registered_is_not_applicable_rather_than_a_failure(self):
        # Arrange — nothing of ours to push from; reporting red here would be a
        # false alarm on every non-MCP caller.
        # Act
        result = evaluate_reachability({"sac"}, set())
        # Assert
        assert result["ok"] is True
        assert "not applicable" in result["detail"]


class TestCheckIsRegisteredInHealth:
    """A check nobody runs is a check that does not exist."""

    def test_health_registers_the_check_under_its_name(self):
        """Assert the WIRING, without executing every other check.

        Calling `health()` here is the obvious move and the wrong one: it runs
        the whole battery, including an install probe that walks site-packages.
        On this container's overlay filesystem that took longer than the rest of
        the file put together and left the run wedged at 100%, so the test said
        nothing about registration and everything about the filesystem. The two
        facts that actually matter — the name is registered, and it is bound to
        OUR function — are both cheap and exact.
        """
        # Arrange
        import inspect

        from scitex_cards import _health

        # Act
        registry_src = inspect.getsource(_health.health)
        # Assert
        assert '"channel_reaches_session"' in registry_src
        assert _health.check_channel_reaches_session is check_channel_reaches_session
