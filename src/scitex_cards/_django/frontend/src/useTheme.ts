import { useEffect, useState } from "react";

export type ColorMode = "light" | "dark";

/** Read the active theme from the SDK shell.
 *
 * The shell sets `<html data-theme="dark|light">` (see SDK theme.css)
 * and toggles it live. We mirror that so React Flow's `colorMode` and the
 * canvas chrome (Background dots, MiniMap) follow the shell instead of being
 * pinned. Defaults to "dark" when the attribute is absent — the board's
 * historical look and the shell's own default on this page. */
function readTheme(): ColorMode {
  if (typeof document === "undefined") return "dark";
  const t = document.documentElement.dataset.theme;
  return t === "light" ? "light" : "dark";
}

export function useTheme(): ColorMode {
  const [mode, setMode] = useState<ColorMode>(readTheme);

  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() => setMode(readTheme()));
    obs.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    // Re-sync once on mount in case the attribute changed before the observer
    // attached (e.g. shell theme init ran after our first render).
    setMode(readTheme());
    return () => obs.disconnect();
  }, []);

  return mode;
}
