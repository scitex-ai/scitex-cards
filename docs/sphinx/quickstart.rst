Quick Start
===========

A minimal ``tasks.yaml``:

.. code-block:: yaml

    tasks:
      - {id: design, title: Design, status: done}
      - {id: build, title: Build, status: in_progress, depends_on: [design]}
      - {id: ship, title: Ship, status: goal, depends_on: [build]}

Render it to a dependency-graph PNG from Python:

.. code-block:: python

    import scitex_cards as cards

    tasks = cards.load_tasks("tasks.yaml")
    mermaid_src = cards.build_mermaid(tasks)
    engine = cards.render(mermaid_src, "tasks.png")   # 'mmdc' or 'kroki'

…or from the shell:

.. code-block:: bash

    # store: $SCITEX_CARDS_DB (PostgreSQL on 55432); unset raises
    scitex-cards render-graph -o tasks.png

    # inspect the generated mermaid without rendering
    scitex-cards render-graph --print-mermaid

    # list the resolved tasks (machine-readable with --json)
    scitex-cards list-tasks --json

Task schema
-----------

Each task in the ``tasks:`` list:

.. list-table::
   :header-rows: 1
   :widths: 18 12 70

   * - Field
     - Required
     - Meaning
   * - ``id``
     - yes
     - unique id, referenced by ``depends_on`` / ``blocks``
   * - ``title``
     - yes
     - short label
   * - ``status``
     - yes
     - ``goal`` | ``pending`` | ``in_progress`` | ``blocked`` | ``done`` | ``deferred`` | ``failed``
   * - ``repo``
     - no
     - owning repo / area
   * - ``depends_on``
     - no
     - ids this task depends on (arrow ``dep --> task``)
   * - ``blocks``
     - no
     - ids this task inhibits (``blocker -- blocks --x task``)
   * - ``note``
     - no
     - free-text annotation
   * - ``priority``
     - no
     - integer rank (lower = higher); document order if absent
   * - ``parent``
     - no
     - id of the task this nests under (drill-down view)

Where your task data lives
--------------------------

``scitex-cards`` ships only the mechanism — no task content. The store is
**PostgreSQL on 55432**, and there is ONE store identity:

1. an explicit ``store`` / ``--store`` argument (wins even if missing)
2. ``$SCITEX_CARDS_DB`` — e.g. ``postgresql://scitex_cards@127.0.0.1:55432/scitex_cards``

Nothing else. Unset **raises**. There is deliberately no SQLite tier, no project
scope (a per-repo store meant one agent saw a different board depending on which
directory it started in), and no bundled ``examples/tasks.yaml`` fallback. Two
backends would be two ways to be wrong about which board you are reading.
