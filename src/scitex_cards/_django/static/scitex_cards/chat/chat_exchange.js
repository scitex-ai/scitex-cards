/* Live delivery-exchange state for messages sent from the Cards web UI. */
(function (root) {
  "use strict";

  function labelFor(exchange) {
    var status = (exchange && exchange.status) || {};
    var code = Number(status.code || 0);
    if (exchange && exchange.final && code >= 200 && code < 300)
      return "Delivered";
    if (exchange && exchange.final) return "Delivery failed";
    if (code === 202) return "Accepted";
    if (code >= 400) return "Delivery problem";
    return "Checking delivery";
  }

  function classFor(exchange) {
    var status = (exchange && exchange.status) || {};
    var code = Number(status.code || 0);
    if (exchange && exchange.final && code >= 200 && code < 300) return "ok";
    if (code >= 400) return "error";
    return "pending";
  }

  function mount(opts) {
    var apiBase = opts.apiBase;
    var viewer = opts.viewer;
    var fetchImpl = opts.fetchImpl || root.fetch;
    var entries = {};

    function paint(id, exchange) {
      var entry = entries[id];
      if (!entry) return;
      entry.exchange = exchange;
      entry.nodes = entry.nodes.filter(function (node) {
        return node.isConnected;
      });
      entry.nodes.forEach(function (node) {
        node.textContent = labelFor(exchange);
        node.className = "exchange-state exchange-state-" + classFor(exchange);
        var status = (exchange && exchange.status) || {};
        node.title = status.message || "No delivery status was returned.";
        node.setAttribute("aria-label", node.title);
      });
    }

    function actionableError(id, response, data) {
      var hint = data && data.check && data.check.hint;
      var message =
        (data && (data.error || (data.status && data.status.message))) ||
        "HTTP " + response.status;
      paint(id, {
        final: response.status === 404,
        status: { code: response.status, message: hint ? message + " " + hint : message },
      });
    }

    function refresh(id) {
      var entry = entries[id];
      if (!entry || (entry.exchange && entry.exchange.final)) return;
      return fetchImpl(apiBase + "/dm/exchange/" + encodeURIComponent(id), {
        headers: { Accept: "application/json" },
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              if (!response.ok) return actionableError(id, response, data);
              paint(id, data);
            });
        })
        .catch(function (error) {
          paint(id, {
            final: false,
            status: {
              code: 503,
              message:
                "Delivery status could not be checked. Verify the network and retry. " +
                error.message,
            },
          });
        });
    }

    function render(meta, message) {
      if (!meta || !message || message.from !== viewer || !message.exchange_id)
        return null;
      var id = String(message.exchange_id);
      var node = document.createElement("button");
      node.type = "button";
      node.className = "exchange-state exchange-state-pending";
      node.textContent = "Checking delivery";
      node.setAttribute("aria-label", "Checking delivery exchange " + id);
      node.addEventListener("click", function () {
        refresh(id);
      });
      if (!entries[id]) entries[id] = { exchange: null, nodes: [] };
      entries[id].nodes.push(node);
      meta.appendChild(node);
      if (entries[id].exchange) paint(id, entries[id].exchange);
      else refresh(id);
      return node;
    }

    function accepted(exchange) {
      if (!exchange || !exchange.exchange_id) return;
      if (!entries[exchange.exchange_id])
        entries[exchange.exchange_id] = { exchange: null, nodes: [] };
      paint(exchange.exchange_id, exchange);
    }

    var timer = root.setInterval(function () {
      Object.keys(entries).forEach(refresh);
    }, 5000);

    return { accepted: accepted, refresh: refresh, render: render, timer: timer };
  }

  var api = { classFor: classFor, labelFor: labelFor, mount: mount };
  root.ChatExchange = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

/* EOF */
