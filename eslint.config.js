// Root ESLint flat config — makes JS linting REAL for the browser scripts
// that ship inside the Django app.
//
// WHAT WAS ACTUALLY WRONG (measured 2026-07-18). A config already existed, but
// only at `src/scitex_cards/_django/frontend/`. ESLint 9 flat config searches
// upward from the linted FILE, so that config covers the Vite app and nothing
// else. The ~15 browser scripts under `static/scitex_cards/board_v3/` and
// `static/scitex_cards/chat/` sit outside it, so the fleet's post-write lint
// hook found no config, exited non-zero, and printed nothing.
//
// That empty failure is the real cost. Clean code looked broken on every JS
// write, which trains the reader to ignore hook output — and a channel people
// have learned to ignore is worse than one that never existed, because the
// next failure that MEANS something gets ignored with it.
//
// WHY THESE RULES AND NOT A PRESET. `js.configs.recommended` needs @eslint/js,
// and the hook runs a GLOBALLY installed eslint with no project node_modules.
// A config that cannot load its own preset fails exactly the way we are fixing.
// So: core rules only, zero dependencies, real findings.
//
// `no-undef` is the one that earns its place here. These files are loaded as
// classic <script> tags with no bundler and no module resolution, so a typo'd
// global (`documnet.getElementById`) is a runtime ReferenceError that no test
// covers — the board simply stops working in the browser. Declaring the
// globals below turns that into a lint error at write time.

const BROWSER_GLOBALS = {
  // Verified against the actual scripts rather than copied from a globals
  // package — if a script starts using something new, `no-undef` will say so,
  // which is the point.
  window: "readonly",
  document: "readonly",
  console: "readonly",
  fetch: "readonly",
  location: "readonly",
  navigator: "readonly",
  localStorage: "readonly",
  sessionStorage: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
  requestAnimationFrame: "readonly",
  getComputedStyle: "readonly",
  CustomEvent: "readonly",
  Event: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  IntersectionObserver: "readonly",
  MutationObserver: "readonly",
  alert: "readonly",
  confirm: "readonly",
  // DUAL-MODE, not a mistake. Several of these scripts are loaded as browser
  // <script> tags AND require()d by the Node tests (see searchSuggest.js's
  // guarded require of searchQuery.js), so CommonJS names legitimately appear
  // beside browser ones. Declaring them keeps `no-undef` reporting real typos
  // instead of the file's own architecture.
  require: "readonly",
  module: "writable",
  exports: "writable",
  self: "readonly",
};

export default [
  {
    // BUILT OUTPUT AND VENDORED CODE ARE NOT OURS TO LINT. `assets/` is Vite
    // output (minified, regenerated every build); `frontend/` keeps its own
    // config and is reached by ESLint's upward search, so linting it from here
    // would apply the wrong rules to TypeScript it cannot parse.
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "src/scitex_cards/_django/static/scitex_cards/assets/**",
      "src/scitex_cards/_django/frontend/**",
      "src/scitex_cards/_sphinx_html/**",
      "**/*.min.js",
    ],
  },
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script", // classic <script> tags, not ES modules
      globals: BROWSER_GLOBALS,
    },
    rules: {
      // WARN, not error — and the reason is architectural, not squeamish.
      //
      // These scripts are loaded as separate classic <script> tags and
      // deliberately SHARE one global scope: `timeline.js` calls `render`,
      // `escapeHtml` and `STATE`, all defined in sibling files loaded before
      // it. ESLint lints one file at a time, so every such reference is
      // reported as undefined. Measured on timeline.js alone: 36 errors, and
      // essentially all of them are this false positive.
      //
      // Shipping that as `error` would make the hook BLOCK every edit to
      // these files — strictly worse than the empty failure being fixed here,
      // because it would block real work rather than merely fail to help.
      // A gate that blocks correct code is one people learn to bypass.
      //
      // As a warning it still surfaces the class that matters (a genuinely
      // typo'd identifier is a runtime ReferenceError no Python test covers)
      // without gating. Promoting it to `error` requires first declaring the
      // shared cross-file globals — carded, not guessed at here.
      "no-undef": "warn",
      // Structural mistakes that are always bugs, never style.
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-unreachable": "error",
      "no-cond-assign": "error",
      "no-func-assign": "error",
      // WARN, not error: these scripts predate this config and an unused
      // variable is untidy rather than broken. Erroring here would make the
      // hook block edits to files whose only sin is age — and a lint gate that
      // blocks unrelated work is one people route around.
      "no-unused-vars": ["warn", { args: "none" }],
    },
  },
];
