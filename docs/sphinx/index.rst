.. scitex-cards documentation master file

scitex-cards — research tasks and agent conversations
=====================================================

**scitex-cards** manages research tasks, agent conversations and progress in
PostgreSQL. Python, CLI, MCP and the browser application expose the same
package-owned operations. Part of `SciTeX <https://scitex.ai>`_.

Configure ``SCITEX_STORE_DSN`` for your PostgreSQL store before running the
examples. They read an existing store; they do not provision a database.
YAML export is a backup/interchange format, not a second runtime backend.

Interfaces
----------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Interface
     - Description
   * - Python API
     - ``import scitex_cards as cards`` — :doc:`api/scitex_cards`
   * - CLI
     - ``scitex-cards <command>`` — :doc:`cli_reference`
   * - MCP
     - AI-agent tools via FastMCP; ``scitex-cards mcp start``
   * - Skills
     - AI-agent knowledge pages bundled at ``_skills/scitex-cards/``

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

   api/scitex_cards

Quick example
-------------

Python API:

.. code-block:: python

    import scitex_cards as cards

    tasks = cards.list_tasks()  # configured PostgreSQL store
    print(f"{len(tasks)} tasks")

CLI:

.. code-block:: bash

    scitex-cards render-graph -o tasks.png      # store -> dependency PNG
    scitex-cards list-tasks --json              # resolved tasks, machine-readable
    scitex-cards board --port 8051              # browser board

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
