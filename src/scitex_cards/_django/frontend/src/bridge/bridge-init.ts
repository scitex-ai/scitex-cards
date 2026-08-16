/** Board bridge init — entry point that mounts the board into #app-mount.
 *
 * Standalone (scitex-cards board): mount immediately; the board IS the whole
 * app (unlike figrecipe's per-file editor, there is no file-click gate).
 * Embedded (scitex-hub workspace): same bundle, fetch override rewrites API
 * paths via MountPoint.
 */

import "../styles/board.css";
import { mountTodoBoard } from "./MountPoint";

function init(): void {
  const mount = document.getElementById("app-mount");
  if (!mount) return;
  const embedded = mount.dataset.embedded === "true";
  mountTodoBoard(mount, embedded);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
