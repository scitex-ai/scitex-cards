/* The composer's own behaviour — the two things the one-row layout needs
 * that CSS cannot express on its own.
 *
 * Operator, verbatim (2026-07-28): 「このスペースの使い方が下手」 and
 * 「telegram を真似てください」. The layout half of that answer is in chat.html
 * (one row: attach, field, emoji at the field's right edge, send). This file
 * is the half that has to run:
 *
 * 1. AUTO-GROW. Telegram's composer is ONE line tall at rest and grows only as
 *    far as you type. The old box reserved a fixed 64px textarea inside a 96px
 *    band whether or not there was anything in it — a permanent tax on the
 *    conversation above. A textarea cannot size itself to its content in CSS,
 *    so the height is measured here.
 *
 * 2. THE LONG-DRAFT OFFER. When the draft crosses the SAME threshold the
 *    thread renderer clamps at (ChatLongText.isLong — imported, never
 *    re-stated, so the two cannot drift), the composer offers to send it as a
 *    .txt file instead. The offer is opt-in: silently turning what someone
 *    typed into an attachment would be worse than the wall of text. The upload
 *    goes through the EXISTING attachment path (ChatAttach -> POST /dm/upload),
 *    so there is one storage layout, one url shape and one renderer — not a
 *    second mechanism that happens to also produce files.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  /* The composer must never own more than this share of the viewport. The
   * whole point of the row is that the CONVERSATION stays visible while you
   * type; a composer that grows without limit reproduces, from the bottom, the
   * exact complaint that long messages produce from the top. */
  var MAX_VIEWPORT_SHARE = 0.35;

  /* Floor for the ceiling: on a very short viewport (a phone in landscape with
   * the keyboard up) 35% can be under one line, and a composer you cannot see
   * your own text in is worse than one that overflows its budget. */
  var MIN_CEILING_PX = 88;

  function stamp() {
    /* Local wall-clock, punctuation stripped: the operator picks this file out
     * of a Downloads folder by when they sent it. */
    var now = new Date();
    function pad(value) {
      return (value < 10 ? "0" : "") + value;
    }
    return (
      String(now.getFullYear()) +
      pad(now.getMonth() + 1) +
      pad(now.getDate()) +
      "-" +
      pad(now.getHours()) +
      pad(now.getMinutes()) +
      pad(now.getSeconds())
    );
  }

  function mount(host) {
    var $form = host.form;
    var $body = host.textarea;
    if (!$form || !$body) return null;
    var longText = host.longText || root.ChatLongText || null;
    var uploadOne = host.uploadOne || null;

    var $bar = document.createElement("div");
    $bar.id = "compose-longtext";
    $bar.hidden = true;
    var $label = document.createElement("span");
    /* Says what will HAPPEN, not just that the draft is big: the thread is
     * going to collapse this to a preview, which is the reason a file might
     * be the better shape for it. */
    $label.textContent = "Long message — the thread will collapse it.";
    var $asFile = document.createElement("button");
    $asFile.type = "button";
    $asFile.textContent = "Send as a .txt file";
    $bar.appendChild($label);
    $bar.appendChild($asFile);
    /* Above the row, inside the form, so it shares the row's column and moves
     * with the composer when the phone keyboard opens. */
    $form.insertBefore($bar, $form.firstChild);

    function ceiling() {
      var viewport = root.innerHeight || 0;
      return Math.max(
        MIN_CEILING_PX,
        Math.round(viewport * MAX_VIEWPORT_SHARE),
      );
    }

    /* Measure at the natural height, then clamp.
     *
     * `height: auto` first is what makes SHRINKING work: scrollHeight never
     * reports less than the current height, so a textarea only ever grows if
     * you skip this. The border is added back because the page is border-box,
     * where `height` includes borders but `scrollHeight` does not — without it
     * every line leaves the field two pixels short and scrolling. */
    function autoGrow() {
      $body.style.height = "auto";
      var borders = $body.offsetHeight - $body.clientHeight;
      var natural = $body.scrollHeight + borders;
      var capped = Math.min(natural, ceiling());
      $body.style.height = capped + "px";
      $body.style.overflowY = natural > capped ? "auto" : "hidden";
    }

    function sync() {
      autoGrow();
      $bar.hidden = !(longText && longText.isLong($body.value));
    }

    function sendAsFile() {
      var text = $body.value;
      if (!text.trim()) return;
      if (!uploadOne) {
        host.showError("Attachments are unavailable on this page.");
        return;
      }
      $asFile.disabled = true;
      uploadOne(
        new File([text], "message-" + stamp() + ".txt", {
          type: "text/plain",
        }),
      )
        .then(function (meta) {
          /* The draft BECOMES the attachment reference — the same single line
           * the picker/paste/drop path writes — so the thread renders it with
           * the renderer that already exists. Nothing is lost: the text the
           * operator typed is what was just uploaded. */
          $body.value = meta.url + "\n";
          sync();
          $body.focus();
        })
        .catch(function (err) {
          host.showError("Could not attach the draft: " + err.message);
        })
        .then(function () {
          $asFile.disabled = false;
        });
    }

    $asFile.addEventListener("click", sendAsFile);
    $body.addEventListener("input", sync);
    /* The viewport share is a share, so a rotation or a keyboard changes it. */
    root.addEventListener("resize", autoGrow);
    sync();

    /* Called by chat.js once a send has cleared the field: clearing `value`
     * from script fires no `input` event, so without this the composer would
     * keep the height of the message it just sent. */
    function reset() {
      $body.style.height = "";
      sync();
    }

    return { autoGrow: autoGrow, reset: reset, sendAsFile: sendAsFile };
  }

  root.ChatCompose = { mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
