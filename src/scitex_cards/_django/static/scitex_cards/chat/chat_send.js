/* The compose SEND path: the submit handler, the Enter binding, and the
 * in-flight re-entry guard.
 *
 * Extracted from chat.js (which had grown past the 512-line cap and could not
 * be edited at all) as the smallest cohesive unit that also contained a P0:
 * the operator could send exactly ONE message per page load.
 *
 * TWO DEFECTS THIS MODULE EXISTS TO FIX, both in the original settle handler:
 *
 * 1. THE GUARD WAS NEVER RELEASED. `sending` was set true before the POST and
 *    the settle handler released only `$send.disabled`. So the flag stayed
 *    true for the life of the page: every later submit returned immediately at
 *    the guard, switching DM peers did not help (the flag is module-level, not
 *    per-thread), and only a full reload cleared it. Measured by the operator
 *    within minutes: 「毎回 Ctrl + Shift + R しています」「DM の相手を変えても
 *    send できない」.
 *
 *    The general shape: a guard whose SET and RELEASE live on different code
 *    paths is one missed branch away from latching forever. Here they are
 *    adjacent, and the release sits in the settle handler that runs on BOTH
 *    the success and the failure path.
 *
 * 2. A FAILED SEND ATE THE TEXT. The textarea is cleared OPTIMISTICALLY before
 *    the POST so a repeated Enter finds an empty box. The original comment
 *    claimed it was "restored verbatim on failure" — but no restore existed,
 *    so anything the operator typed was lost whenever the send failed, which
 *    is exactly when they most want it back.
 *
 * Collaborators are passed in rather than reached for, so the module runs
 * under node against stubs — same shape as chat_select.js / chat_diff.js.
 */
(function (root) {
  "use strict";

  function mount(opts) {
    var $form = opts.form;
    var $body = opts.textarea;
    var $send = opts.send;
    var apiBase = opts.apiBase;
    var getPeer = opts.getPeer;
    var composer = opts.composer || null;
    var onSent = opts.onSent || function () {};
    var clearError = opts.clearError || function () {};
    var showError = opts.showError || function () {};
    var fetchImpl = opts.fetchImpl || root.fetch;

    // Re-entry guard. `$send.disabled` does NOT prevent re-entry on its own:
    // Enter calls `$form.requestSubmit()`, which runs the submit handler
    // whether or not the BUTTON is disabled. With the textarea also cleared
    // only after the response landed, every extra Enter during the round trip
    // re-sent the same text — the operator diagnosed that one themselves
    // (「Enter を連発すると何個も送られる」).
    var sending = false;

    function settle() {
      // BOTH halves of the guard, in the handler that runs on every path.
      // Releasing one without the other is the defect this module replaced.
      sending = false;
      $send.disabled = false;
      $body.focus();
    }

    function sendMessage(event) {
      if (event && event.preventDefault) event.preventDefault();
      if (sending) return;
      var peer = getPeer();
      if (!peer) return;
      var text = $body.value.trim();
      if (!text) return;

      sending = true;
      $send.disabled = true;
      // Clear optimistically so a repeated Enter finds an empty box and
      // returns early. Restored below if the send does not land.
      $body.value = "";
      if (composer) composer.reset();

      return fetchImpl(apiBase + "/dm/thread/" + encodeURIComponent(peer), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: text }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp
              .json()
              .catch(function () {
                return {};
              })
              .then(function (data) {
                throw new Error(data.error || "HTTP " + resp.status);
              });
          }
          clearError();
          onSent();
        })
        .catch(function (err) {
          // Put the text back. Clearing `value` from script fires no `input`
          // event, so the composer is told explicitly.
          if (!$body.value) $body.value = text;
          if (composer) composer.reset();
          showError("Send failed: " + err.message);
        })
        .then(settle);
    }

    $form.addEventListener("submit", sendMessage);

    // Enter sends; Shift+Enter inserts a newline (phone keyboards send via the
    // button anyway — this is for desktop convenience).
    $body.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $form.requestSubmit();
      }
    });

    return {
      send: sendMessage,
      isSending: function () {
        return sending;
      },
    };
  }

  root.ChatSend = { mount: mount };
})(typeof window !== "undefined" ? window : globalThis);

/* EOF */
