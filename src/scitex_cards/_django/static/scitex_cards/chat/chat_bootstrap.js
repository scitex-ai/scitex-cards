/* SDK-owned filter first, then the existing classic DM controller. */
(function () {
  "use strict";
  var entry = document.currentScript;
  var app = document.getElementById("cards-dm-app");
  if (!entry || !app) throw new Error("DM bootstrap requires its declared app root");
  var sdkUrl = entry.getAttribute("data-sdk-filter-url");
  var controllerUrl = entry.getAttribute("data-chat-controller-url");
  if (!sdkUrl || !controllerUrl) throw new Error("DM bootstrap requires declared asset URLs");
  // This is a classic defer script: it registers before DOMContentLoaded.
  // The promise also handles a cached/late SDK import after that event fires.
  var ready = document.readyState === "complete" ? Promise.resolve() :
    new Promise(function (resolve) {
      document.addEventListener("DOMContentLoaded", resolve, { once: true });
    });
  import(sdkUrl).then(function (sdk) {
    var fields = sdk.mountFilterFields(app);
    var field = fields.find(function (item) { return item.input.id === "agent-filter"; });
    if (!field) throw new Error("SDK did not construct the declared agent filter");
    field.input.setAttribute("aria-controls", "agent-list");
    return ready;
  }).then(function () {
    var controller = document.createElement("script");
    controller.src = controllerUrl;
    controller.async = false;
    controller.setAttribute("data-cards-chat-controller", "");
    document.body.appendChild(controller);
  }).catch(function (error) {
    var notice = document.getElementById("error-bar");
    if (notice) {
      notice.textContent = "The shared filter could not load. Reload to retry.";
      notice.style.display = "block";
    }
    console.error("DM bootstrap failed", error);
  });
})();
