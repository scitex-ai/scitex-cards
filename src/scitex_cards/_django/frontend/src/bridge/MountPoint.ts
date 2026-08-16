/** Mount/unmount the board React app + embedded fetch override.
 *
 * When embedded in a scitex-cloud / scitex-hub workspace, API paths
 * (/graph, /tasks, /ping) are rewritten to /apps/scitex-cards/scitex-cards/...
 * so the same bundle works standalone and embedded (figrecipe parity).
 */

import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TodoBoard } from "../TodoBoard";

const SLUG = "scitex-cards";
const API_PATHS = ["/graph", "/tasks", "/ping"];

let root: Root | null = null;
let overrideInstalled = false;

function installFetchOverride(): void {
  if (overrideInstalled) return;
  const original = window.fetch;
  window.fetch = function (input: RequestInfo | URL, init?: RequestInit) {
    if (typeof input === "string" && input.startsWith("/")) {
      const pathOnly = input.split("?")[0];
      const isApi = API_PATHS.some(
        (p) => pathOnly === p || pathOnly.startsWith(p),
      );
      if (isApi) {
        input = `/apps/${SLUG}/${SLUG}${input}`;
      }
    }
    return original.call(window, input, init);
  };
  overrideInstalled = true;
}

export function mountTodoBoard(
  container: HTMLElement,
  embedded: boolean,
): void {
  if (embedded) installFetchOverride();
  if (root) {
    root.unmount();
    root = null;
  }
  root = createRoot(container);
  root.render(createElement(TodoBoard));
}

export function unmountTodoBoard(): void {
  if (root) {
    root.unmount();
    root = null;
  }
}
