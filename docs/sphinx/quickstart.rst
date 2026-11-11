Quick Start
===========

Connect to a configured PostgreSQL store before running these examples. Set
``SCITEX_STORE_DSN`` explicitly for the intended database. Store discovery is
provided by scitex-dev; these commands do not create or provision a database.

Read existing cards through the public Python API:

.. code-block:: python

    import scitex_cards as cards

    rows = cards.list_tasks(status="in_progress")
    print(f"{len(rows)} cards in progress")

Creating cards also requires a registered assignee and a creator identity.
See :doc:`api/scitex_cards` for ``add_task`` and its identity arguments.

Inspect the dependency graph without invoking a renderer:

.. code-block:: bash

    scitex-cards render-graph --print-mermaid
    scitex-cards list-tasks --json

To write a PNG, choose a destination and run:

.. code-block:: bash

    scitex-cards render-graph -o tasks.png

Rendering uses local ``mmdc`` when available, with a network fallback to
``kroki.io``. See :doc:`installation` for rendering requirements.

Task schema
-----------

Cards returned by ``list_tasks`` include these fields:

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
     - ``goal`` | ``in_progress`` | ``blocked`` | ``done`` | ``deferred`` | ``failed`` | ``cancelled``
   * - ``assignee``
     - yes for creation
     - registered user responsible for the card
   * - ``created_by``
     - yes for creation
     - creator identity; may be resolved from ``SCITEX_CARDS_AGENT_ID``
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

Card data lives in PostgreSQL. An explicit ``store=`` argument selects the
store for a Python call. Otherwise scitex-dev's shared-store resolver selects
the database, including ``SCITEX_STORE_DSN`` configuration. A project directory
does not supply a separate Cards task store.

Set ``SCITEX_STORE_DSN`` to the intended PostgreSQL connection string before
starting the CLI or browser application. Database credentials, provisioning
and access permissions are deployment configuration. YAML exports are for
backup/interchange; they are not a second runtime backend.
