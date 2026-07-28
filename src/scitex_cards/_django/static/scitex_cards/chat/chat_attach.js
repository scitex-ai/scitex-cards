/* Chat attachments — upload via picker, clipboard paste, or drag-drop.
 *
 * Lifted verbatim out of chat.js when that file hit its 512-line budget. The
 * behaviour is unchanged; only its address is.
 *
 * Three ways in, one path out: picker, clipboard paste and drag-drop all call
 * uploadFiles, which appends the returned URL as its own line in the body.
 * Uploading BEFORE send means a failed upload never produces a message that
 * references a file that is not there.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  function mount(host) {
    var $body = host.composerEl;
    var $attach = host.attachEl;
    var $file = host.fileEl;
    if (!$body) return null;

    function uploadFiles(files) {
      var list = Array.prototype.slice.call(files || []).filter(Boolean);
      if (!list.length) return;
      host.clearError();
      list.forEach(function (file) {
        var form = new FormData();
        form.append("file", file);
        fetch(host.apiBase + "/dm/upload", { method: "POST", body: form })
          .then(function (resp) {
            return resp.json().then(function (data) {
              if (!resp.ok)
                throw new Error(data.error || "HTTP " + resp.status);
              return data;
            });
          })
          .then(function (data) {
            var sep = $body.value && !/\n$/.test($body.value) ? "\n" : "";
            $body.value += sep + data.url + "\n";
            $body.focus();
          })
          .catch(function (err) {
            host.showError("Upload failed: " + err.message);
          });
      });
    }

    if ($attach && $file) {
      $attach.addEventListener("click", function () {
        $file.click();
      });
      $file.addEventListener("change", function () {
        uploadFiles($file.files);
        $file.value = "";
      });
    }

    $body.addEventListener("paste", function (event) {
      var items = (event.clipboardData || {}).files;
      if (items && items.length) {
        event.preventDefault();
        uploadFiles(items);
      }
    });

    ["dragover", "drop"].forEach(function (name) {
      $body.addEventListener(name, function (event) {
        event.preventDefault();
        if (name === "drop") uploadFiles(event.dataTransfer.files);
      });
    });

    return { uploadFiles: uploadFiles };
  }

  root.ChatAttach = { mount: mount };
})(typeof self !== "undefined" ? self : this);

/* EOF */
