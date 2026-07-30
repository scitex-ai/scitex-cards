/* Markdown rendering for DM message bodies.
 *
 * Operator request 2026-07-29: every substantive message in this board is
 * written in markdown and rendered as literal asterisks, backticks and pipes.
 * Incident reports arrive as tables and code fences shown as plain text.
 *
 * THE SECURITY DECISION, because it shapes everything else.
 *
 * Message bodies are UNTRUSTED: any authenticated caller on the DM rail can
 * write one, and it is rendered into the operator's browser. The usual
 * markdown approach - build an HTML string, escape the dangerous parts - makes
 * safety depend on getting every escape right in every branch, forever. One
 * missed path is stored XSS on the board the operator lives in.
 *
 * So this renderer NEVER PRODUCES AN HTML STRING. It builds DOM nodes, and all
 * text arrives through `document.createTextNode`, which cannot become markup by
 * construction - there is no parse step for an attacker to reach. `innerHTML`,
 * `insertAdjacentHTML` and `outerHTML` do not appear in this file, and a test
 * asserts that.
 *
 * That is a stronger guarantee than escaping and a cheaper one to keep: a
 * future contributor adding a new inline rule cannot introduce an injection
 * unless they first switch the module to string-building, which the test
 * catches.
 *
 * LINKS are the one place a node can still carry an executable payload, via
 * `javascript:` / `data:` / `vbscript:` in href. Only http, https and mailto
 * are turned into anchors; anything else renders as plain text, visibly
 * unlinked rather than silently dropped.
 *
 * SUBSET: fenced code, inline code, headings, bullet/ordered lists, tables,
 * blockquotes, bold, italic, links, paragraphs. Chosen from what this thread
 * actually contains, not from a spec. Anything unrecognised falls through as
 * text, so an unsupported construct is legible rather than mangled.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ChatMarkdown = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Schemes that may become a real anchor. Everything else stays text. */
  var SAFE_SCHEME = /^(https?:|mailto:)/i;

  function textNode(s) {
    return document.createTextNode(s);
  }

  function el(tag, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  /* ---- inline ---------------------------------------------------------- */

  /* Inline rules, applied in order. Each returns [node, consumedLength] or
   * null. `code` is FIRST so backticks win over emphasis inside them - the
   * common case in this board is a code span containing underscores. */
  var INLINE = [
    // `code`
    function (s) {
      var m = /^`([^`]+)`/.exec(s);
      if (!m) return null;
      var node = el("code");
      node.appendChild(textNode(m[1]));
      return [node, m[0].length];
    },
    // [label](url)
    function (s) {
      var m = /^\[([^\]]*)\]\(([^)\s]+)\)/.exec(s);
      if (!m) return null;
      var label = m[1] || m[2];
      if (!SAFE_SCHEME.test(m[2])) {
        // Not a scheme we will link. Show the label as plain text rather than
        // dropping it - the operator should see that something was there.
        return [textNode(label), m[0].length];
      }
      var a = el("a");
      a.setAttribute("href", m[2]);
      a.setAttribute("rel", "noopener noreferrer");
      a.setAttribute("target", "_blank");
      a.appendChild(textNode(label));
      return [a, m[0].length];
    },
    // **bold**
    function (s) {
      var m = /^\*\*([^*]+)\*\*/.exec(s);
      if (!m) return null;
      var node = el("strong");
      inlineInto(node, m[1]);
      return [node, m[0].length];
    },
    // *italic*  (single asterisk, not part of **)
    function (s) {
      var m = /^\*([^*]+)\*/.exec(s);
      if (!m) return null;
      var node = el("em");
      inlineInto(node, m[1]);
      return [node, m[0].length];
    },
    // bare url
    function (s) {
      var m = /^(https?:\/\/[^\s<>()]+)/.exec(s);
      if (!m) return null;
      var a = el("a");
      a.setAttribute("href", m[1]);
      a.setAttribute("rel", "noopener noreferrer");
      a.setAttribute("target", "_blank");
      a.appendChild(textNode(m[1]));
      return [a, m[0].length];
    },
  ];

  /* Append the inline-parsed form of `text` into `parent`. */
  function inlineInto(parent, text) {
    var s = String(text == null ? "" : text);
    var buffer = "";
    while (s.length) {
      var hit = null;
      for (var i = 0; i < INLINE.length; i++) {
        hit = INLINE[i](s);
        if (hit) break;
      }
      if (hit) {
        if (buffer) {
          parent.appendChild(textNode(buffer));
          buffer = "";
        }
        parent.appendChild(hit[0]);
        s = s.slice(hit[1]);
      } else {
        buffer += s.charAt(0);
        s = s.slice(1);
      }
    }
    if (buffer) parent.appendChild(textNode(buffer));
    return parent;
  }

  /* ---- blocks ---------------------------------------------------------- */

  function isTableRow(line) {
    return /^\s*\|.*\|\s*$/.test(line);
  }

  function isTableDivider(line) {
    return /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.indexOf("-") !== -1;
  }

  function splitRow(line) {
    var trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split("|").map(function (c) {
      return c.trim();
    });
  }

  function renderTable(lines, start, out) {
    var head = splitRow(lines[start]);
    var table = el("table", "md-table");
    var thead = el("thead");
    var hrow = el("tr");
    head.forEach(function (c) {
      inlineInto(hrow.appendChild(el("th")), c);
    });
    thead.appendChild(hrow);
    table.appendChild(thead);
    var tbody = el("tbody");
    var i = start + 2;
    for (; i < lines.length && isTableRow(lines[i]); i++) {
      var row = el("tr");
      splitRow(lines[i]).forEach(function (c) {
        inlineInto(row.appendChild(el("td")), c);
      });
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    out.appendChild(table);
    return i;
  }

  function renderFence(lines, start, out) {
    var body = [];
    var i = start + 1;
    for (; i < lines.length && !/^\s*```/.test(lines[i]); i++) body.push(lines[i]);
    var pre = el("pre", "md-pre");
    var code = el("code");
    // Raw text, verbatim, as a text node: a fence containing markup is shown,
    // never parsed. This is the case a string-building renderer gets wrong.
    code.appendChild(textNode(body.join("\n")));
    pre.appendChild(code);
    out.appendChild(pre);
    return i + 1; // skip the closing fence
  }

  function renderList(lines, start, out, ordered) {
    var list = el(ordered ? "ol" : "ul", "md-list");
    var pattern = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
    var i = start;
    for (; i < lines.length; i++) {
      var m = pattern.exec(lines[i]);
      if (!m) break;
      inlineInto(list.appendChild(el("li")), m[1]);
    }
    out.appendChild(list);
    return i;
  }

  /* Render `text` into a DocumentFragment of real nodes. */
  function render(text) {
    var out = document.createDocumentFragment();
    var lines = String(text == null ? "" : text).split("\n");
    var i = 0;
    var paragraph = null;

    function flush() {
      if (paragraph) {
        out.appendChild(paragraph);
        paragraph = null;
      }
    }

    while (i < lines.length) {
      var line = lines[i];

      if (/^\s*```/.test(line)) {
        flush();
        i = renderFence(lines, i, out);
        continue;
      }
      if (isTableRow(line) && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
        flush();
        i = renderTable(lines, i, out);
        continue;
      }
      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        flush();
        var h = el("h" + heading[1].length, "md-h");
        inlineInto(h, heading[2]);
        out.appendChild(h);
        i++;
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        flush();
        i = renderList(lines, i, out, false);
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        flush();
        i = renderList(lines, i, out, true);
        continue;
      }
      var quote = /^\s*>\s?(.*)$/.exec(line);
      if (quote) {
        flush();
        var bq = el("blockquote", "md-quote");
        inlineInto(bq, quote[1]);
        out.appendChild(bq);
        i++;
        continue;
      }
      if (!line.trim()) {
        flush();
        i++;
        continue;
      }
      // Plain text line: accumulate into a paragraph, keeping the author's
      // line breaks (this board's messages are written with meaningful ones).
      if (!paragraph) paragraph = el("p", "md-p");
      else paragraph.appendChild(el("br"));
      inlineInto(paragraph, line);
      i++;
    }
    flush();
    return out;
  }

  return { render: render, inlineInto: inlineInto, SAFE_SCHEME: SAFE_SCHEME };
});

/* EOF */
