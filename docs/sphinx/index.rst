.. scitex-todo documentation master file

scitex-todo — canonical YAML task store with pluggable adapters
===============================================================

**scitex-todo** keeps your tasks in one validated YAML store (a top-level
``tasks:`` list) and renders that single source of truth through pluggable
adapters: a mermaid dependency-graph PNG, a read-only React-Flow web board,
and a plain task listing. Part of `SciTeX <https://scitex.ai>`_.

The store is **PostgreSQL on 55432** and there is ONE store identity: an explicit
``store`` argument → ``$SCITEX_CARDS_DB``. Nothing else — unset RAISES. There is
deliberately no SQLite tier, no project scope and no bundled-example fallback: an
unconfigured store is a configuration error, not a cue to invent a board.

Interfaces
----------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Interface
     - Description
   * - Python API
     - ``import scitex_todo as todo`` — :doc:`api/scitex_todo`
   * - CLI
     - ``scitex-todo <command>`` — :doc:`cli_reference`
   * - MCP
     - AI-agent tools via fastmcp *(on the roadmap)*
   * - Skills
     - AI-agent knowledge pages bundled at ``_skills/scitex-todo/``

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   cli_reference

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/scitex_todo

Quick example
-------------

Python API:

.. code-block:: python

    import scitex_todo as todo

    tasks = todo.load_tasks("tasks.yaml")    # validates id / title / status
    mermaid_src = todo.build_mermaid(tasks)  # YAML -> flowchart TB
    engine = todo.render(mermaid_src, "tasks.png")
    print(f"rendered via {engine}")

CLI:

.. code-block:: bash

    scitex-todo render-graph -o tasks.png      # YAML -> dependency PNG
    scitex-todo list-tasks --json              # resolved tasks, machine-readable
    scitex-todo board --port 8051              # read-only web board (needs [web])

Four Freedoms for Research
--------------------------

0. The freedom to **run** your research anywhere -- your machine, your terms.
1. The freedom to **study** how every step works -- from raw data to final manuscript.
2. The freedom to **redistribute** your workflows, not just your papers.
3. The freedom to **modify** any module and share improvements with the community.

AGPL-3.0 -- because research infrastructure deserves the same freedoms as the software it runs on.

Indices and tables
===================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
