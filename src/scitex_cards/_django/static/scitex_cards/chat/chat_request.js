/* CSRF headers shared by DM mutations, including embedded Hub mounts.
 * Read the rendered token first: HttpOnly/session CSRF cookies are unreadable.
 * Standalone exempt endpoints remain compatible when no token is available.
 */
(function (root) {
  "use strict";
  function headers(json) {
    var result = json ? { "Content-Type": "application/json" } : {};
    if (typeof document === "undefined") return result;
    var input = document.querySelector('[name="csrfmiddlewaretoken"]');
    var meta = document.querySelector('meta[name="csrf-token"]');
    var cookie = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    var token = (input && input.value) || (meta && meta.content) ||
      (cookie ? decodeURIComponent(cookie[1]) : "");
    if (token) result["X-CSRFToken"] = token;
    return result;
  }
  root.ChatRequest = { headers: headers };
})(typeof window !== "undefined" ? window : globalThis);
