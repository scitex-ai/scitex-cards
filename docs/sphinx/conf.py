"""Sphinx configuration for scitex-todo documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath("../../src"))

# -- Project information -----------------------------------------------------

project = "scitex-todo"
copyright = "2026, Yusuke Watanabe"
author = "Yusuke Watanabe"

try:
    from importlib.metadata import version as _get_version

    release = _get_version("scitex-todo")
except Exception:
    release = "0.2.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_rtd_theme",
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_autodoc_typehints",
]

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Optional / web-extra and MCP deps that need not be importable on RTD.
autodoc_mock_imports = [
    "django",
    "scitex_app",
    "scitex_ui",
    "fastmcp",
]

autosummary_generate = True

# Napoleon (numpy/google docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_use_param = True
napoleon_use_rtype = True
# Render an ``Attributes`` numpydoc section as inline ``:ivar:`` fields in the
# class docstring body instead of standalone ``.. py:attribute::`` directives.
# autodoc already documents the dataclass fields (``undoc-members``); without
# this, napoleon's attribute directives collide with those, yielding "duplicate
# object description of ...AgentInfo.<field>" warnings under ``-W``.
napoleon_use_ivar = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "to_claude/**"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# reST noise in autodoc'd docstrings shouldn't fail a `-W` build.
suppress_warnings = ["docutils"]

# -- HTML output -------------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "prev_next_buttons_location": "bottom",
}
html_static_path = ["_static"]
html_title = f"{project} v{release}"
html_short_title = project

html_context = {
    "display_github": True,
    "github_user": "scitex-ai",
    "github_repo": "scitex-cards",
    "github_version": "develop",
    "conf_py_path": "/docs/sphinx/",
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "html_image",
    "smartquotes",
    "substitution",
    "tasklist",
]

# -- intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
