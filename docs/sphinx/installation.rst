Installation
============

.. code-block:: bash

    # Recommended -- uv resolver
    uv pip install "scitex-cards[all]"

    # Plain pip also works
    pip install scitex-cards

Requires Python >= 3.10.

Extras
------

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Extra
     - Adds
   * - ``web``
     - Compatibility extra for Django + scitex-sdk; both are core dependencies
   * - ``docs``
     - Sphinx + RTD theme for building this documentation
   * - ``dev``
     - pytest, pytest-cov, scitex-dev (test + audit toolchain)
   * - ``all``
     - ``dev``, ``docs``, ``mcp``, ``web``, ``currency`` and ``postgres``

Django, scitex-sdk, FastMCP and the PostgreSQL driver are core dependencies.
The ``web``, ``mcp`` and ``postgres`` extras remain accepted compatibility names.
Installation does not provision a PostgreSQL server or configure access.

Rendering backends
------------------

PNG output from ``scitex-cards render-graph`` needs **one** of:

- ``mmdc`` (mermaid-cli, with a puppeteer/playwright chromium) on ``PATH``, or
- outbound access to ``kroki.io`` (the automatic fallback).

Printing the mermaid source (``scitex-cards render-graph --print-mermaid``)
needs neither.

Verify
------

.. code-block:: bash

    python -c "import scitex_cards; print(scitex_cards.__version__)"
    scitex-cards --help
