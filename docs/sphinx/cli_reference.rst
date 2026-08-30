CLI Reference
=============

.. code-block:: bash

    scitex-cards --help
    scitex-cards --help-recursive    # flattened help for every subcommand

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - Command
     - Purpose
   * - ``render-graph [-o PNG] [--tasks PATH] [--print-mermaid]``
     - Render the dependency graph to PNG (or print mermaid source).
   * - ``list-tasks [--tasks PATH] [--json]``
     - List resolved tasks (id / status / title).
   * - ``board [--port N] [--tasks PATH] [--no-browser]``
     - Launch the read-only web board (needs the ``[web]`` extra).
   * - ``list-python-apis [-v/-vv/-vvv] [--json]``
     - Introspect the public Python API.
   * - ``mcp list-tools [--json]``
     - List MCP tools (none yet — on the roadmap).
   * - ``skills {list, get, install}``
     - List / print / install the bundled agent skills.
   * - ``install-shell-completion [--shell bash|zsh|fish]``
     - Install tab-completion (cache-file pattern).
   * - ``print-shell-completion [--shell ...]``
     - Print the completion script for eval / sourcing.

Universal flags
---------------

- ``-h``, ``--help`` — usage with an example (every command).
- ``--help-recursive`` — flatten help for all subcommands (top level).
- ``--json`` — machine-readable output on every data-reading command.
- ``-V``, ``--version`` — print ``scitex-cards/X.Y.Z``.

Every command resolves the store the same way, and there is ONE store identity:
``--store`` → ``$SCITEX_CARDS_DB`` (PostgreSQL on 55432). Nothing else — unset
raises. No second-engine tier, no project scope, no bundled-example fallback.
