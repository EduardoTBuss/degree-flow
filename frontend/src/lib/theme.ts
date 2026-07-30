import { useCallback, useEffect, useState } from "react";

/**
 * A1 theme (ADR-022): light | dark | system preference, persisted in
 * localStorage["fluxo.theme"], applied as `data-theme` on <html>.
 * All colors live as CSS custom properties in styles.css — this module
 * only flips one attribute; it never touches component styles.
 */
export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "fluxo.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export function readStoredTheme(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // Storage unavailable (private mode / blocked) — fall back to system.
  }
  return "system";
}

function resolve(pref: ThemePreference): "light" | "dark" {
  if (pref === "system") return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
  return pref;
}

/** Sets data-theme on <html>. Also called at boot (main.tsx + inline index.html script). */
export function applyTheme(pref: ThemePreference): void {
  document.documentElement.dataset.theme = resolve(pref);
}

export function useTheme(): { theme: ThemePreference; setTheme: (t: ThemePreference) => void } {
  const [theme, setThemeState] = useState<ThemePreference>(readStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    // "system" tracks the OS live: re-resolve whenever the media query flips.
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: ThemePreference) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, t);
    } catch {
      // Persisting is best-effort; theme still applies for the session.
    }
    setThemeState(t);
  }, []);

  return { theme, setTheme };
}
