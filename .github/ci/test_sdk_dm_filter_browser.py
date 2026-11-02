"""Real Chromium integration for the DM bootstrap and public SDK FilterField.

Run explicitly with the dev extra and installed Chromium. The small consumer
records the bootstrap seam; filtering and keyboard handlers are shipped code.
No service, store, messages, or mocked SDK implementation is involved.
"""

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture()
def browser_page(tmp_path):
    from scitex_sdk import get_frontend_package_dir

    # Arrange: serve the installed public SDK and the real Cards modules.
    sdk = Path(get_frontend_package_dir()) / "ui/static/scitex_sdk"
    cards = Path(__file__).resolve().parents[2] / "src/scitex_cards/_django/static/scitex_cards"
    (tmp_path / "sdk").symlink_to(sdk, target_is_directory=True)
    (tmp_path / "cards").symlink_to(cards, target_is_directory=True)
    (tmp_path / "consumer.js").write_text("""
      window.consumerSawReady = window.domReadySeen === true;
      window.capturedInput = document.getElementById('agent-filter');
      if (!capturedInput) throw new Error('Controller started before SDK input');
      window.filter = ChatFilter.mount({input: capturedInput,
        list: document.getElementById('agent-list')});
    """)
    (tmp_path / "index.html").write_text("""
      <div id="cards-dm-app">
        <div id="agents"><div data-stx-filter-field data-filter-subject="agents"
          data-filter-id="agent-filter"></div>
          <div id="agent-list"><div class="agent"><span class="name">dev-helper</span></div>
          <div class="agent"><span class="name">lead</span></div></div></div>
      </div>
      <div id="error-bar" hidden></div><textarea id="composer"></textarea>
      <script defer src="/sdk/ui/js/app/combobox.js"></script>
      <script defer src="/cards/chat/chat_filter.js"></script>
      <script defer src="/cards/chat/chat_bootstrap.js"
        data-sdk-filter-url="/sdk/ui/js/app/filter-field.js"
        data-chat-controller-url="/consumer.js"></script>
    """)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ.get("SCITEX_CARDS_TEST_CHROMIUM")
            )
            try:
                page = browser.new_page()
                page.add_init_script("document.addEventListener('DOMContentLoaded', "
                                     "() => window.domReadySeen = true)")
                yield page, f"http://127.0.0.1:{server.server_port}/index.html"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("delayed_import", [False, True])
def test_sdk_filter_bootstrap_poll_and_keyboard(browser_page, delayed_import):
    # Arrange: cached and post-DOMContentLoaded import paths use the same owner.
    page, url = browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if delayed_import:
        def delay_sdk(route):
            page.wait_for_timeout(150)
            route.continue_()
        page.route("**/filter-field.js", delay_sdk)
    # Act: the actual bootstrap mounts public SDK before starting its consumer.
    page.goto(url)
    page.wait_for_function("window.capturedInput && window.filter")
    field = page.locator("#agent-filter")
    # Assert: public input/hint/shortcuts; real fuzzy matcher and poll observer.
    assert page.evaluate("window.consumerSawReady") is True
    assert field.get_attribute("type") == "search"
    assert field.get_attribute("aria-label") == "Filter agents"
    assert page.locator(".stx-filter-field__hint").count() == 1
    page.locator("#composer").focus()
    page.keyboard.press("Control+k")
    assert field.evaluate("el => el === document.activeElement")
    field.fill("dvhlp")
    assert page.locator(".agent:visible").count() == 1
    assert page.locator(".agent:visible").inner_text() == "dev-helper"
    page.evaluate("""document.getElementById('agent-list').innerHTML =
      '<div class="agent"><span class="name">lead</span></div>' +
      '<div class="agent"><span class="name">dev-helper</span></div>'""")
    page.wait_for_function("document.querySelector('.agent').style.display === 'none'")
    assert field.input_value() == "dvhlp"
    field.press("Escape")
    assert field.input_value() == ""
    assert not field.evaluate("el => el === document.activeElement")
    assert page.locator(".agent:visible").count() == 2
    page.locator("#agents").evaluate("el => el.style.display='none'")
    page.locator("#composer").focus()
    page.keyboard.press("Control+k")
    assert page.locator("#composer").evaluate("el => el === document.activeElement")
    page.keyboard.press("Control+Shift+k")
    page.keyboard.press("Control+Alt+k")
    assert page.locator("#composer").evaluate("el => el === document.activeElement")
    page.keyboard.type("/k")
    assert page.locator("#composer").input_value() == "/k"
    assert errors == []


def test_actual_board_launcher_yields_to_sdk_and_hidden_panes(browser_page):
    # Arrange: execute the actual template function, not a copied shortcut rule.
    page, url = browser_page
    source = (Path(__file__).resolve().parents[2] /
              "src/scitex_cards/_django/templates/scitex_cards/_board_foot.html").read_text()
    begin = source.index("    function attachSearchKeyboardLauncher()")
    end = source.index("    attachSearchKeyboardLauncher();", begin)
    actual_launcher = source[begin:end] + "attachSearchKeyboardLauncher();"
    page.goto(url)
    page.wait_for_function("window.filter")
    page.evaluate("""document.body.insertAdjacentHTML('beforeend',
      '<div id="board"><input id="f-search"></div>')""")
    page.evaluate("() => {" + actual_launcher + "}")
    # Act / Assert: visible SDK capture wins even with both controls visible.
    page.locator("#composer").focus()
    page.keyboard.press("Control+k")
    assert page.locator("#agent-filter").evaluate("el => el === document.activeElement")
    # Act / Assert: a hidden board cannot steal from the composer either.
    page.locator("#agents").evaluate("el => el.style.display='none'")
    page.locator("#board").evaluate("el => el.style.display='none'")
    page.locator("#composer").focus()
    page.keyboard.press("Control+k")
    assert page.locator("#composer").evaluate("el => el === document.activeElement")
    # Act / Assert: the original board shortcut still works when it alone is visible.
    page.locator("#board").evaluate("el => el.style.display='block'")
    page.keyboard.press("Control+k")
    assert page.locator("#f-search").evaluate("el => el === document.activeElement")
