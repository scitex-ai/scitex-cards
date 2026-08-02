#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A login PAGE, because the browser will not show what the protocol carries.

WHY THIS EXISTS, measured rather than assumed. The 401 challenge names its own
source correctly at the protocol level::

    WWW-Authenticate: Basic realm="SciTeX Cards - password is
                      SCITEX_CARDS_PASSWORD on the server", charset="UTF-8"

and the response body spells out where to read the value. Verified with curl,
which prints both.

CHROME PRINTS NEITHER. Its Basic-auth dialog shows only "Sign in" and the
origin — the realm string was removed years ago because an attacker-controlled
realm is a phishing surface. So the operator saw a bare username/password box
with no hint at all, on a board he owns, and could not get in:

    「これ良く分からん、入れなくなった、どこにパスワードある？
      user は ywatanabe? hint がないからわからん」

The fix from earlier the same day was correct in the header and INVISIBLE to the
only person using it. I verified it with the tool that shows the realm and never
with the browser that does not — and this container has no browser, so the
honest report at the time was "unverified in a browser", which I did not give.

THE SPLIT, and it is the whole design:

    a BROWSER (Accept: text/html)  -> this page. We control every pixel, so the
                                      instructions are simply ON it.
    anything else (curl, scripts)  -> the Basic 401 in ``_board_auth``, unchanged.
                                      That path works, and the realm IS visible
                                      to a tool that prints headers.

Neither is a fallback for the other: they are two audiences with different
rendering, and the mechanism follows the audience rather than the reverse.

WHAT THIS IS NOT. It is not a user system. There is still ONE password and the
username is still not consulted — that arrives with the ssh-shaped auth
primitive and its ``users/<name>/`` registry. This page exists so that today's
single password is FINDABLE, and it deliberately says so rather than implying a
richer model it does not have.
"""

from __future__ import annotations

import secrets

from django.core import signing
from django.http import HttpResponse

__all__ = [
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "SIGNING_SALT",
    "cookie_is_valid",
    "login_page",
    "wants_html",
]

#: The signed cookie a successful form login sets. Signed rather than a bare
#: flag: an unsigned "logged_in=1" is a password anyone can type into devtools.
COOKIE_NAME = "scitex_cards_session"
SIGNING_SALT = "scitex-cards.board-login"

#: 14 days. Long enough that the operator is not re-prompted on a board he keeps
#: open for weeks; short enough that a stolen laptop cookie expires on its own.
COOKIE_MAX_AGE = 14 * 24 * 60 * 60


def wants_html(request) -> bool:
    """True when this looks like a browser navigation rather than a tool.

    ``Accept: text/html`` is the standard signal and the one browsers actually
    send on navigation. Deliberately NOT user-agent sniffing: the User-Agent
    string is a decades-long history of clients lying about who they are, and
    Accept states what the caller can RENDER, which is the actual question.
    """
    accept = request.META.get("HTTP_ACCEPT", "")
    return "text/html" in accept


def cookie_is_valid(request) -> bool:
    """True when the request carries a session cookie we signed and it is fresh.

    ``getattr`` rather than ``request.COOKIES`` directly, and this is a semantic
    choice rather than defensive padding: a request object carrying no cookie
    jar HAS no cookie, so ``False`` is the correct answer and not a swallowed
    error. Raising here would make "no cookies" indistinguishable from "the
    cookie is bad", and only one of those is an exceptional condition — neither
    is.

    Caught by an existing test, which builds a minimal request as
    ``type("R", (), {"META": {}})()``. Adding COOKIES to that stub would have
    fixed the symptom by making the test model this function's needs; the stub
    was right and the read was too narrow.
    """
    raw = (getattr(request, "COOKIES", None) or {}).get(COOKIE_NAME)
    if not raw:
        return False
    try:
        signing.loads(raw, salt=SIGNING_SALT, max_age=COOKIE_MAX_AGE)
    except signing.BadSignature:
        # Covers tampering AND expiry (SignatureExpired subclasses it). Both
        # mean the same thing to the caller: this cookie does not authenticate.
        return False
    return True


def issue_cookie(response: HttpResponse, *, secure: bool) -> HttpResponse:
    """Attach a fresh signed session cookie.

    ``secure`` is passed in rather than read here, because whether the
    connection is HTTPS is a property of the request and this function should
    not have to guess at proxy headers.
    """
    response.set_cookie(
        COOKIE_NAME,
        signing.dumps({"v": 1}, salt=SIGNING_SALT),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=secure,
    )
    return response


_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SciTeX Cards — sign in</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; background:#14161a; color:#e6e6e6;
         font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ width:min(34rem,92vw); background:#1c1f24; border:1px solid #2c3038;
         border-radius:10px; padding:1.6rem 1.8rem 1.8rem; }}
  h1 {{ font-size:1.15rem; margin:0 0 .2rem; }}
  .sub {{ color:#9aa3af; font-size:.85rem; margin:0 0 1.2rem; }}
  label {{ display:block; font-size:.8rem; color:#9aa3af; margin:0 0 .3rem; }}
  input {{ width:100%; box-sizing:border-box; padding:.6rem .7rem; font-size:1rem;
          background:#0f1114; color:#e6e6e6; border:1px solid #343a44;
          border-radius:6px; }}
  input:focus {{ outline:2px solid #4c8dff; outline-offset:1px; }}
  button {{ margin-top:1.1rem; width:100%; padding:.65rem; font-size:1rem;
           font-weight:600; background:#3b6fd4; color:#fff; border:0;
           border-radius:6px; cursor:pointer; }}
  button:hover {{ background:#4c8dff; }}
  .where {{ margin-top:1.5rem; padding-top:1.1rem; border-top:1px solid #2c3038;
           font-size:.83rem; color:#9aa3af; }}
  .where b {{ color:#e6e6e6; font-weight:600; }}
  code {{ display:block; margin:.45rem 0 0; padding:.5rem .6rem; background:#0f1114;
         border:1px solid #262b33; border-radius:5px; color:#cbd3de;
         font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
         overflow-x:auto; white-space:pre; }}
  .warn {{ margin-top:1.1rem; font-size:.8rem; color:#c9a227; }}
  .err {{ margin:0 0 1rem; padding:.55rem .7rem; border-radius:6px;
         background:#3a1d1d; border:1px solid #5c2b2b; color:#f0b3b3;
         font-size:.85rem; }}
</style>
<main>
  <h1>SciTeX Cards</h1>
  <p class="sub">This board asks for a password. Here is where to find it.</p>
  {error}
  <form method="post">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autofocus
           autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
  <div class="where">
    <b>There is no username.</b> This board has one shared password today; the
    field browsers show for it is ignored. Per-user logins arrive with the
    ssh-shaped auth work.
    <p style="margin:.9rem 0 0"><b>The password is</b> the value of
    <code style="display:inline;padding:.1rem .3rem">{env_var}</code> in the
    environment of the process serving this board. To read it on that machine:</p>
    <code>systemctl --user show scitex-todo.dashboard.service -p Environment
grep -rh {env_var} ~/.config/systemd/user/</code>
  </div>
  <p class="warn">If you did not set this password, do not type one. A prompt
  that cannot tell you where its answer lives has the same shape as a phishing
  prompt — ask whoever runs this board first.</p>
</main>
"""


def login_page(*, env_var: str, error: str = "") -> HttpResponse:
    """The sign-in page, carrying the instructions the browser dialog swallowed.

    The status is 200 rather than 401 ON PURPOSE. A 401 with an HTML body makes
    the browser open its native Basic dialog ON TOP of this page — which is the
    dialog with no hint in it, so the user would be looking at the exact prompt
    this page exists to replace, with the explanation hidden behind it.
    """
    banner = f'<div class="err">{error}</div>' if error else ""
    return HttpResponse(
        _PAGE.format(error=banner, env_var=env_var),
        content_type="text/html; charset=utf-8",
        status=200,
    )


def password_matches(supplied: str | None, password: str) -> bool:
    """Constant-time compare of a submitted password. ``None`` never matches."""
    if not supplied:
        return False
    return secrets.compare_digest(supplied, password)


# EOF
