/* chat_avatar.js — the deterministic per-agent avatar, as pure data.
 *
 * Pure module (chat_diff.js's contract): returns a SPEC, never a DOM node, so
 * node can exercise the real file. chat.js builds the element from it.
 *
 * Extracted while chat.js was over its 512-line cap. The logic was already pure
 * and had no tests — a hash and a word-split producing something the operator
 * sees on every row, verified by nobody. Moving it made it testable, which is a
 * better reason than the line count that prompted it.
 *
 * The shared "scitex-" prefix carries no identity, so it is stripped before
 * initials are taken: otherwise every agent in the fleet would read "SC".
 */

//: Hue is taken modulo this. Saturation/lightness stay fixed so no name can
//: produce an unreadable colour against the dark surface.
const HUE_SPACE = 360;
const AVATAR_SAT = "55%";
const AVATAR_LIGHT = "42%";

/** A stable 32-bit hash of `name`. Same name always yields the same hue. */
function hashName(name) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash;
}

/** Up to two initials from the name's DISTINCTIVE words. */
function initialsFor(name) {
  const words = (name || "")
    .replace(/^scitex-/, "")
    .split(/[-_]+/)
    .filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join("");
}

/** `{initials, background}` for `name`. Initials fall back to "?" so a blank
 *  or prefix-only name still renders a visible chip rather than an empty box. */
function avatarSpec(name) {
  return {
    initials: initialsFor(name) || "?",
    background:
      "hsl(" + (hashName(name) % HUE_SPACE) + ", " + AVATAR_SAT + ", " +
      AVATAR_LIGHT + ")",
  };
}

const _chatAvatarApi = { hashName, initialsFor, avatarSpec };

if (typeof globalThis !== "undefined") {
  globalThis.STX = globalThis.STX || {};
  globalThis.STX.chatAvatar = _chatAvatarApi;
  globalThis.ChatAvatar = _chatAvatarApi;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = _chatAvatarApi;
}
