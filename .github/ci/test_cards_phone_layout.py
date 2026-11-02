"""Real-browser leaf layout contracts inside an explicitly sized host frame.

Run with the dev extra and installed Chromium. This fixture uses the actual
Django page, CSS and Details controller; it makes no API or store requests.
The host's separate mobile frame contract is not emulated by this test.
"""

import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture()
def layout_page(tmp_path):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")
    import django
    from django.template.loader import render_to_string
    from scitex_sdk import get_frontend_package_dir

    django.setup()
    root = Path(__file__).resolve().parents[2]
    cards = root / "src/scitex_cards/_django"
    static = tmp_path / "static"
    static.mkdir()
    (static / "scitex_cards").symlink_to(cards / "static/scitex_cards")
    (static / "scitex_sdk").symlink_to(
        Path(get_frontend_package_dir()) / "ui/static/scitex_sdk"
    )
    rendered = render_to_string("scitex_cards/chat.html", {"api_base": "/", "csrf_token": "a" * 64})
    head = rendered[rendered.index("<head>"):rendered.index("</head>")]
    head = re.sub(r"/\*.*?\*/|<!--.*?-->", "", head, flags=re.S)
    head = re.sub(r"<script\b.*?</script>", "", head, flags=re.S)
    head = re.sub(r'<link[^>]*href="https?://[^>]*>', "", head)
    section = re.search(r'<section id="cards-dm-app".*?</section>', rendered, re.S).group()
    section = re.sub(r"<script\b.*?</script>", "", section, flags=re.S)
    source = (cards / "templates/scitex_cards/_board_foot.html").read_text()
    start = source.index('    const DETAILS_PANE_KEY =')
    end = source.index('    // SDK content-tabs owns appearance;', start)
    controller = source[start:end]
    html = head + "</head><body><div id='fixture-frame'>" + section + "</div>"
    html += "<style>#fixture-frame{height:600px;width:100%}</style>"
    html += "<script>" + controller + "_syncDetailsPane();</script></body>"
    (tmp_path / "index.html").write_text(html)
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
                page = browser.new_page(viewport={"width": 390, "height": 844})
                yield page, f"http://127.0.0.1:{server.server_port}/index.html"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_phone_tabs_do_not_expand_to_fill_unused_space(layout_page):
    # Arrange
    page, url = layout_page
    page.goto(url)
    # Act
    geometry = page.evaluate("""() => {
      const root = document.querySelector('#cards-dm-app').getBoundingClientRect();
      const tabs = document.querySelector('.stx-cards-header').getBoundingClientRect();
      return {root: root.height, tabs: tabs.height};
    }""")
    # Assert
    assert geometry == {"root": 600, "tabs": 44}


def test_phone_board_and_details_share_a_reachable_scroll_region(layout_page):
    # Arrange
    page, url = layout_page
    page.goto(url)
    page.evaluate("""() => {
      document.querySelector('#dm-pane').hidden = true;
      document.querySelector('#board-pane').hidden = false;
    }""")
    # Act
    page.locator('#details-toggle').click()
    page.locator('#details-tab-board').scroll_into_view_if_needed()
    observation = page.locator('#details-tab-board').evaluate("""el => {
      const r = el.getBoundingClientRect(), main = document.querySelector('#board-pane .main');
      const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
      return {reachable: hit === el || el.contains(hit),
        scrollable: main.scrollHeight > main.clientHeight,
        overflow: getComputedStyle(main).overflowY};
    }""")
    # Assert
    assert observation == {"reachable": True, "scrollable": True, "overflow": "auto"}


def test_details_button_collapse_survives_bubbling_and_rail_still_expands(layout_page):
    # Arrange
    page, url = layout_page
    page.goto(url)
    page.evaluate("""() => {
      document.querySelector('#dm-pane').hidden = true;
      document.querySelector('#board-pane').hidden = false;
    }""")
    button = page.locator('#details-toggle')
    rail = page.locator('#details-panel')
    # Act
    button.click()
    expanded = button.get_attribute('aria-expanded')
    button.click()
    collapsed = rail.evaluate("el => el.classList.contains('collapsed')")
    rail.click(position={"x": 3, "y": 3})
    expanded_by_rail = button.get_attribute('aria-expanded')
    button.click()
    rail.focus()
    page.keyboard.press('Enter')
    expanded_by_enter = button.get_attribute('aria-expanded')
    button.click()
    rail.focus()
    page.keyboard.press('Space')
    expanded_by_space = button.get_attribute('aria-expanded')
    # Assert
    assert (expanded, collapsed, expanded_by_rail, expanded_by_enter,
            expanded_by_space) == ('true', True, 'true', 'true', 'true')
