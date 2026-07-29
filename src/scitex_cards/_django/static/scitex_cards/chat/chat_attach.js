/* Chat attachments — upload via picker, clipboard paste, or drag-drop, AND the
 * rendering of what that upload produced.
 *
 * Lifted verbatim out of chat.js when that file hit its 512-line budget. The
 * behaviour is unchanged; only its address is. The RENDER half (splitBody /
 * nodeFor) followed the same way and for the same reason, and lands in the
 * right place: the url shape is one decision, so the code that writes it and
 * the code that reads it belong in one file rather than either side being free
 * to change its mind alone.
 *
 * Three ways in, one path out: picker, clipboard paste and drag-drop all call
 * uploadFiles, which appends the returned URL as its own line in the body.
 * Uploading BEFORE send means a failed upload never produces a message that
 * references a file that is not there.
 *
 * `mount` is per-page state; `splitBody` and `nodeFor` are statics on the
 * module, so a renderer can use them without mounting anything.
 *
 * Plain browser JS, no build step, no dependencies (line-limit
 * discipline: js <512 lines).
 */
(function (root) {
  "use strict";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* An attachment is carried as its own line in the body: a relative URL under
   * `attachments/`. Deliberately NOT a new sidecar field — the DM store is
   * append-only and widening its record mid-incident is the kind of change
   * that has cost this board data before. The body is already the source of
   * truth, so a line IS the reference, and an older client still shows
   * something meaningful (the path) instead of nothing. */
  var IMAGE_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;

  /* Split a message body into its prose and its attachment references. */
  function splitBody(body) {
    var lines = String(body || "").split("\n");
    var text = [];
    var files = [];
    lines.forEach(function (line) {
      var t = line.trim();
      if (t.indexOf("attachments/") === 0) files.push(t);
      else text.push(line);
    });
    return { text: text.join("\n").trim(), files: files };
  }

  /* One attachment reference as a node: an inline image, or a download link. */
  function nodeFor(apiBase, relUrl) {
    var href = apiBase + "/" + relUrl;
    var name = relUrl.split("/").pop();
    if (IMAGE_RE.test(name)) {
      var a = el("a", "att-img");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener";
      var img = document.createElement("img");
      img.src = href;
      img.alt = name;
      img.loading = "lazy";
      a.appendChild(img);
      return a;
    }
    var link = el("a", "att-file", "📎 " + name);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }

  function mount(host) {
    var $body = host.composerEl;
    var $attach = host.attachEl;
    var $file = host.fileEl;
    if (!$body) return null;

    /* ONE file to the store, returning its metadata — the whole HTTP half,
     * with no opinion about what the caller then does with the url.
     *
     * Extracted so a second caller can reuse the endpoint instead of writing
     * its own POST: chat_compose.js offers to send an over-long DRAFT as a
     * .txt, and it must land in the same place, with the same url shape, for
     * the thread's existing renderer to show it. A duplicated fetch would have
     * been a second mechanism producing files that merely look the same. */
    function uploadOne(file) {
      var form = new FormData();
      form.append("file", file);
      return fetch(host.apiBase + "/dm/upload", {
        method: "POST",
        body: form,
      }).then(function (resp) {
        return resp.json().then(function (data) {
          if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
          return data;
        });
      });
    }

    function uploadFiles(files) {
      var list = Array.prototype.slice.call(files || []).filter(Boolean);
      if (!list.length) return;
      host.clearError();
      list.forEach(function (file) {
        uploadOne(file)
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

    return { uploadFiles: uploadFiles, uploadOne: uploadOne };
  }

  root.ChatAttach = { mount: mount, splitBody: splitBody, nodeFor: nodeFor };
})(typeof self !== "undefined" ? self : this);

/* EOF */
