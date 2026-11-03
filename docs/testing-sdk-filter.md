# DM filter integration checks

Cards requires SDK 0.3.17 or newer. Its empty `data-stx-filter-field` mount
hands input, icon, hint, Ctrl+K/Cmd+K and Escape ownership to the SDK. The
classic deferred `chat_bootstrap.js` mounts that field before loading `chat.js`,
which captures the input. `chat_filter.js` retains list filtering through the
SDK Combobox matcher and reapplies it after polling replaces the list.

The changed entry assets use the SDK `app_static` content hashes. The app does
not override host workspace columns or sidebars in its phone stylesheet.

Install and run the regular rendered-response and module checks:

```sh
pip install -e '.[dev]'
pytest tests/scitex_cards/_django/test__chat_agent_filter.py \
  tests/scitex_cards/_django/static/scitex_cards/chat/test_chat_filter.py \
  tests/scitex_cards/_django/test__chat_reaction_forward_wiring.py \
  tests/scitex_cards/test__board_v3_signatures.py
```

The regular test harness provisions its own guarded PostgreSQL schemas. Never
point it at the live Cards store.

Run the supplemental real-browser component integration explicitly:

```sh
python -m playwright install chromium
pytest .github/ci/test_sdk_dm_filter_browser.py --noconftest
```

`SCITEX_CARDS_TEST_CHROMIUM` can select an existing Chromium executable. The
fixture serves the installed public SDK and Cards modules on loopback, with a
small test consumer that records input availability before mounting the actual
Cards list filter. It checks cached and delayed SDK imports, fuzzy matching,
list replacement, Escape, and arbitration with the actual board launcher.
This component fixture opens no database and sends no messages. It supplements
the rendered-template tests; it does not certify a deployed backend or a live
conversation.
