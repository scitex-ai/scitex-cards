/* timelineSelect.js — multi-select + copy + right-click menu for the
 * board_v3 "Timeline" raster (operator request 2026-06-26):
 *   "allow us to click multiple markers and copy them as contents; the
 *    same for one scatter plot; right click should have menu".
 *
 * timeline.js is at the per-file line cap, so this interaction layer ships
 * as a SEPARATE classic <script defer> (mirrors timelinePack.js /
 * timelineGate.js / timelineGeo.js). Loaded BEFORE timeline.js; it wires
 * itself to the page via event delegation on #columns so it survives the
 * raster's innerHTML rebuilds without timeline.js having to call it.
 *
 * Markers: SVG circles `.tl-dot` (agent/project rasters) and `.tl-card`
 * divs (the simple list). Each carries its task id via the inline
 * `openDetail('<id>')` onclick that timeline.js already emits; we read the
 * id back out of that attribute so timeline.js needs no change.
 *
 * Behaviour:
 *   • single click   → toggle the marker into a client-side selection Set,
 *                       add `.tl-dot--selected` / `.tl-card--selected`.
 *                       We intercept in the CAPTURE phase and stopPropagation
 *                       so the marker's own `onclick=openDetail()` does NOT
 *                       fire — selection and open-details thus coexist.
 *   • double click    → open the card's detail drawer (openDetail).
 *   • click empty bg  → clear the selection.
 *   • right click     → context menu: Copy contents / Open details /
 *                       (when >1 selected) Copy N selected.
 *
 * Pure helpers (formatCardCopy / joinCopyBlocks) are node-testable and are
 * exported via window.STX.timelineSelect + module.exports (mirrors the other
 * timeline* siblings). No build step — served static.
 */
"use strict";

(function () {
  // ── pure: card → copy text ─────────────────────────────────────────
  // A card is the merged shape {id,title,status,agent/assignee,note}. The
  // raster marker only carries {id,title,status,agent}; the caller merges
  // in STATE.graph fields (note/assignee) before formatting when available.
  // Returns a stable, human-readable block — one labelled line each.
  function formatCardCopy(card) {
    card = card || {};
    var id = card.id == null ? "" : String(card.id);
    var title = card.title || card.task || id || "(untitled)";
    var status = card.status || "?";
    var assignee = card.assignee || card.agent || "—";
    var note = card.note == null ? "" : String(card.note).trim();
    var lines = [
      "id: " + id,
      "title: " + title,
      "status: " + status,
      "assignee: " + assignee,
      "note: " + (note || "—"),
    ];
    return lines.join("\n");
  }

  // Join multiple card blocks with a blank line between each.
  function joinCopyBlocks(cards) {
    if (!Array.isArray(cards)) return "";
    return cards.map(formatCardCopy).join("\n\n");
  }

  // Publish the pure helpers early (node + page).
  var _api = { formatCardCopy: formatCardCopy, joinCopyBlocks: joinCopyBlocks };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelineSelect = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
    return; // node import path: no DOM wiring
  }
  if (typeof document === "undefined") return;

  // ── selection state ────────────────────────────────────────────────
  var SELECTED = new Set(); // task ids

  function _toast(msg, err) {
    if (typeof window.toast === "function") window.toast(msg, err);
  }
  function _esc(s) {
    return typeof window.escapeHtml === "function"
      ? window.escapeHtml(s)
      : String(s == null ? "" : s);
  }

  // Read the task id off a marker element from its inline openDetail onclick.
  function _idOf(el) {
    if (!el) return null;
    if (el.dataset && el.dataset.tlId) return el.dataset.tlId;
    var oc = el.getAttribute && el.getAttribute("onclick");
    if (oc) {
      var m = oc.match(/openDetail\((['"])(.*?)\1\)/);
      if (m) return m[2];
    }
    return null;
  }

  function _markerFrom(target) {
    if (!target || !target.closest) return null;
    return target.closest(".tl-dot, .tl-card");
  }

  // Resolve a richer card object: marker title/status/agent merged with the
  // STATE.graph node (note/assignee/project) by id when reachable.
  function _cardFor(id, markerEl) {
    var node = null;
    try {
      var nodes = (window.STATE && STATE.graph && STATE.graph.nodes) || [];
      node = nodes.find(function (n) {
        return String(n.id) === String(id);
      });
    } catch (e) {}
    var fromMarker = {};
    if (markerEl) {
      var titleEl = markerEl.querySelector
        ? markerEl.querySelector("title")
        : null;
      if (titleEl && titleEl.textContent) {
        // raster <title> is "TITLE\nstatus: X\n..." — first line is the title
        fromMarker.title = titleEl.textContent.split("\n")[0];
      }
    }
    var card = {
      id: id,
      title: (node && (node.title || node.task)) || fromMarker.title || id,
      status: (node && node.status) || fromMarker.status || "?",
      assignee: (node && (node.assignee || node.agent)) || "—",
      note: (node && node.note) || "",
    };
    return card;
  }

  // ── visual selection toggle ─────────────────────────────────────────
  function _applySelClass(el, on) {
    if (!el) return;
    var cls =
      el.classList && el.classList.contains("tl-card")
        ? "tl-card--selected"
        : "tl-dot--selected";
    if (el.classList) el.classList.toggle(cls, on);
  }

  function _refreshSelClasses() {
    // Re-apply after a raster rebuild: clear all, then re-mark selected ones.
    var host = document.getElementById("columns");
    if (!host) return;
    host
      .querySelectorAll(".tl-dot--selected, .tl-card--selected")
      .forEach(function (el) {
        el.classList.remove("tl-dot--selected", "tl-card--selected");
      });
    if (!SELECTED.size) return;
    host.querySelectorAll(".tl-dot, .tl-card").forEach(function (el) {
      var id = _idOf(el);
      if (id != null && SELECTED.has(String(id))) _applySelClass(el, true);
    });
  }

  function _toggle(id, el) {
    id = String(id);
    if (SELECTED.has(id)) {
      SELECTED.delete(id);
      _applySelClass(el, false);
    } else {
      SELECTED.add(id);
      _applySelClass(el, true);
    }
  }

  function _clear() {
    SELECTED.clear();
    _refreshSelClasses();
  }

  // ── copy ────────────────────────────────────────────────────────────
  function _copyText(text, label) {
    function ok() {
      _toast("✓ " + label);
    }
    try {
      if (navigator.clipboard && window.isSecureContext !== false) {
        navigator.clipboard.writeText(text).then(ok, function (e) {
          _toast("✗ copy failed: " + (e && e.message), true);
        });
        return;
      }
    } catch (e) {}
    // Fallback: hidden textarea + execCommand (older / non-HTTPS browsers).
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      ok();
    } catch (e2) {
      _toast("✗ copy failed: " + (e2 && e2.message), true);
    }
  }

  function _copyOne(id, markerEl) {
    var text = formatCardCopy(_cardFor(id, markerEl));
    _copyText(text, "Copied 1 card");
  }

  function _copySelected() {
    var ids = Array.from(SELECTED);
    if (!ids.length) return;
    var cards = ids.map(function (id) {
      return _cardFor(id, _findMarker(id));
    });
    _copyText(joinCopyBlocks(cards), "Copied " + cards.length + " card(s)");
  }

  function _findMarker(id) {
    var host = document.getElementById("columns");
    if (!host) return null;
    var all = host.querySelectorAll(".tl-dot, .tl-card");
    for (var i = 0; i < all.length; i++) {
      if (String(_idOf(all[i])) === String(id)) return all[i];
    }
    return null;
  }

  // ── context menu (mirror board_v3.html closeCardCtx / item(...)) ─────
  var _menuEl = null;
  function _closeMenu() {
    if (_menuEl) {
      _menuEl.remove();
      _menuEl = null;
    }
  }
  function _menuEsc(e) {
    if (e.key === "Escape") _closeMenu();
  }

  function _openMenu(e, id, markerEl) {
    e.preventDefault();
    _closeMenu();
    // ensure the right-clicked marker is part of the selection context
    if (!SELECTED.has(String(id))) {
      // right-click without prior selection acts on just this card
    }
    var card = _cardFor(id, markerEl);
    var menu = document.createElement("div");
    menu.className = "card-ctx-menu"; // reuse the board's menu styling
    var item = function (label, fn, danger) {
      return (
        '<button class="card-ctx-item' +
        (danger ? " card-ctx-item--danger" : "") +
        '" onclick="' +
        fn +
        '">' +
        label +
        "</button>"
      );
    };
    var idJs = String(id).replace(/'/g, "\\'");
    var html =
      '<div class="card-ctx-title">' +
      _esc(card.title || id) +
      "</div>" +
      item(
        "Copy contents",
        "STX.timelineSelect._menuCopyOne('" + idJs + "')",
      ) +
      item("Open details", "STX.timelineSelect._menuOpen('" + idJs + "')");
    if (SELECTED.size > 0) {
      html +=
        '<div class="card-ctx-sep"></div>' +
        item(
          "Copy " + SELECTED.size + " selected",
          "STX.timelineSelect._menuCopySelected()",
        );
    }
    menu.innerHTML = html;
    document.body.appendChild(menu);
    var mw = menu.offsetWidth,
      mh = menu.offsetHeight;
    var vw = window.innerWidth,
      vh = window.innerHeight;
    menu.style.left = Math.min(e.clientX, vw - mw - 6) + "px";
    menu.style.top = Math.min(e.clientY, vh - mh - 6) + "px";
    _menuEl = menu;
    setTimeout(function () {
      document.addEventListener("click", _closeMenu, { once: true });
      document.addEventListener("keydown", _menuEsc, { once: true });
    }, 0);
  }

  // Menu-item callbacks (referenced from inline onclick in the menu html).
  _api._menuCopyOne = function (id) {
    _closeMenu();
    _copyOne(id, _findMarker(id));
  };
  _api._menuOpen = function (id) {
    _closeMenu();
    if (typeof window.openDetail === "function") window.openDetail(id);
  };
  _api._menuCopySelected = function () {
    _closeMenu();
    _copySelected();
  };

  // Set by the marquee handler right before the synthetic background click
  // that ends a drag, so _onClickCapture below does NOT wipe the selection
  // the rubber-band just filled.
  var _suppressNextBgClear = false;

  // ── event delegation on #columns (survives raster rebuilds) ─────────
  function _host() {
    return document.getElementById("columns");
  }
  function _isTimeline() {
    return (
      typeof window.STATE !== "undefined" &&
      STATE &&
      STATE.layout === "timeline"
    );
  }

  function _onClickCapture(e) {
    if (!_isTimeline()) return;
    var marker = _markerFrom(e.target);
    if (!marker) {
      // A marquee drag ends with a background click; the marquee just filled
      // SELECTED, so skip the clear for that one synthetic event.
      if (_suppressNextBgClear) {
        _suppressNextBgClear = false;
        return;
      }
      // click on empty timeline background → clear selection
      if (e.target && e.target.closest && e.target.closest("#columns")) {
        if (SELECTED.size) _clear();
      }
      return;
    }
    var id = _idOf(marker);
    if (id == null) return;
    // Intercept BEFORE the marker's own onclick=openDetail fires.
    e.preventDefault();
    e.stopPropagation();
    _toggle(id, marker);
  }

  function _onDblClick(e) {
    if (!_isTimeline()) return;
    var marker = _markerFrom(e.target);
    if (!marker) return;
    var id = _idOf(marker);
    if (id == null) return;
    e.preventDefault();
    if (typeof window.openDetail === "function") window.openDetail(id);
  }

  function _onContextMenu(e) {
    if (!_isTimeline()) return;
    var marker = _markerFrom(e.target);
    if (!marker) return;
    var id = _idOf(marker);
    if (id == null) return;
    _openMenu(e, id, marker);
  }

  // Ctrl/Cmd+C while ≥1 marker is selected on the timeline → copy the whole
  // selection to the clipboard WITHOUT opening any card. Skips when the focus
  // is in a text input / contentEditable so normal text copy still works.
  function _onKeyDown(e) {
    if (!_isTimeline()) return;
    if (!(e.key === "c" || e.key === "C")) return;
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    if (!SELECTED.size) return;
    var ae = document.activeElement;
    if (ae) {
      var tag = (ae.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || ae.isContentEditable) return;
    }
    // Only hijack copy when the user hasn't manually highlighted page text.
    var sel = window.getSelection && window.getSelection();
    if (sel && String(sel).length) return;
    e.preventDefault();
    _copySelected();
  }

  // ── marquee (rubber-band) selection ─────────────────────────────────
  // Drag across EMPTY raster space (not starting on a dot) to draw a box;
  // every .tl-dot whose centre falls inside it is added to SELECTED on
  // mouseup. Plain left-drag only — keeps single-click toggle + dblclick-open
  // intact. Coords are in the SVG's user space (cx/cy are unitless px there),
  // derived from the SVG's bounding rect so we don't depend on a viewBox.
  var SVG_NS = "http://www.w3.org/2000/svg";
  var _mq = null; // { svg, rect, x0, y0 } while a drag is live

  function _svgPoint(svg, e) {
    var r = svg.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function _onMouseDown(e) {
    if (e.button !== 0 || !_isTimeline()) return;
    if (!e.target || !e.target.closest) return;
    if (e.target.closest(".tl-dot")) return; // a dot → let click-toggle run
    var svg = e.target.closest(".tl-svg");
    if (!svg) return;
    var p = _svgPoint(svg, e);
    var rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("class", "tl-marquee");
    svg.appendChild(rect);
    _mq = { svg: svg, rect: rect, x0: p.x, y0: p.y, moved: false };
    document.addEventListener("mousemove", _onMouseMove, true);
    document.addEventListener("mouseup", _onMouseUp, true);
  }

  function _onMouseMove(e) {
    if (!_mq) return;
    var p = _svgPoint(_mq.svg, e);
    var x = Math.min(p.x, _mq.x0),
      y = Math.min(p.y, _mq.y0);
    var w = Math.abs(p.x - _mq.x0),
      h = Math.abs(p.y - _mq.y0);
    if (w > 3 || h > 3) _mq.moved = true;
    _mq.rect.setAttribute("x", x);
    _mq.rect.setAttribute("y", y);
    _mq.rect.setAttribute("width", w);
    _mq.rect.setAttribute("height", h);
    if (_mq.moved && e.preventDefault) e.preventDefault();
  }

  function _onMouseUp() {
    var mq = _mq;
    _mq = null;
    document.removeEventListener("mousemove", _onMouseMove, true);
    document.removeEventListener("mouseup", _onMouseUp, true);
    if (!mq) return;
    var x = +mq.rect.getAttribute("x") || 0,
      y = +mq.rect.getAttribute("y") || 0;
    var w = +mq.rect.getAttribute("width") || 0,
      h = +mq.rect.getAttribute("height") || 0;
    if (mq.rect.parentNode) mq.rect.parentNode.removeChild(mq.rect);
    if (!mq.moved || (w < 3 && h < 3)) return; // a click, not a drag
    mq.svg.querySelectorAll(".tl-dot").forEach(function (dot) {
      var cx = +dot.getAttribute("cx"),
        cy = +dot.getAttribute("cy");
      if (cx >= x && cx <= x + w && cy >= y && cy <= y + h) {
        var id = _idOf(dot);
        if (id != null) {
          SELECTED.add(String(id));
          _applySelClass(dot, true);
        }
      }
    });
    // The trailing background click that closes the drag must NOT clear it.
    _suppressNextBgClear = true;
  }

  function _install() {
    var host = _host() || document;
    // capture phase so we beat the marker's inline onclick
    host.addEventListener("click", _onClickCapture, true);
    host.addEventListener("dblclick", _onDblClick, false);
    host.addEventListener("contextmenu", _onContextMenu, false);
    host.addEventListener("mousedown", _onMouseDown, false);
    document.addEventListener("keydown", _onKeyDown, false);
    // re-mark selected dots after each raster rebuild
    var mo = new MutationObserver(function () {
      if (_isTimeline() && SELECTED.size) _refreshSelClasses();
    });
    if (_host()) mo.observe(_host(), { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _install);
  } else {
    _install();
  }
})();
