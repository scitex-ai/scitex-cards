# Cards mobile content layout

The SDK owns the outer viewport frame. Cards fills that supplied height; it
must not create another viewport-height box below the host header. Its Board/DM
navigation row does not grow to consume unused vertical space.

On a phone the Board canvas and Details panel stack inside the Board content
scroll region. The canvas keeps its existing readable height floor; scrolling
that region exposes Details rather than clipping its controls. The existing
Details button collapses once per click; a collapsed rail still expands through
its own click, Enter or Space. These controls retain their saved preference.

The host dock remains an overlay. Cards adds no fabricated clearance, reserved
strip or dock preference changes. The host's mobile content-scroll behavior
continues to own transient dock collapse.

The leaf contract can be tested without a live service or store:

```sh
python -m pytest -q .github/ci/test_cards_phone_layout.py
```

Install the declared `dev` extra and Chromium first. Set
`SCITEX_CARDS_TEST_CHROMIUM` when using an existing Chromium executable. The
fixture renders the actual Django page and loads the actual CSS and Details
controller inside an explicitly sized frame. It checks leaf layout and native
DOM events; qualification of the SDK's real mobile frame is a separate check.
