"""Real Chromium and Django CSRF regression; no store is accessed.

Run: python .github/ci/check_dm_csrf.py [Chromium executable]
"""

import json
import secrets
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

from django.conf import settings

settings.configure(
    SECRET_KEY=secrets.token_hex(32),
    DEBUG=False,
    ALLOWED_HOSTS=["127.0.0.1"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["django.middleware.csrf.CsrfViewMiddleware"],
    CSRF_COOKIE_HTTPONLY=True,
)

import django  # noqa: E402

django.setup()

from django.core.wsgi import get_wsgi_application  # noqa: E402
from django.http import HttpResponse, JsonResponse  # noqa: E402
from django.middleware.csrf import get_token  # noqa: E402
from django.urls import path  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "src/scitex_cards/_django/static/scitex_cards/chat/chat_request.js"


def page(request):
    token = get_token(request)
    return HttpResponse(
        '<input name="csrfmiddlewaretoken" type="hidden" value="'
        + token
        + '"><script>'
        + JS.read_text()
        + "</script>"
    )


def mutation(request):
    return JsonResponse({"accepted": request.method == "POST"})


urlpatterns = [path("", page), path("mutation", mutation)]


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


# Arrange: a protected endpoint with an HttpOnly cookie, unreadable to JS.
server = make_server(
    "127.0.0.1", 0, get_wsgi_application(), handler_class=Quiet
)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with sync_playwright() as p:
        options = {"executable_path": sys.argv[1]} if len(sys.argv) > 1 else {}
        browser = p.chromium.launch(**options)
        tab = browser.new_page()
        tab.goto(f"http://127.0.0.1:{server.server_port}/")
        # Act: omit the token, then use the actual served DM request helper.
        result = tab.evaluate("""async () => {
            const bad = await fetch('/mutation', {method: 'POST'});
            const good = await fetch('/mutation', {
                method: 'POST', headers: ChatRequest.headers(true), body: '{}'
            });
            return {
                without: bad.status, withToken: good.status,
                cookieHidden: !document.cookie.includes('csrftoken'),
                accepted: (await good.json()).accepted
            };
        }""")
        browser.close()
    # Assert: existing CSRF protection passes without exposing the cookie.
    assert result == {
        "without": 403, "withToken": 200, "cookieHidden": True, "accepted": True
    }, result
    print(json.dumps(result))
finally:
    server.shutdown()
    server.server_close()
    thread.join()
