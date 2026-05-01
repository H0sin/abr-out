import { useEffect, useState } from "react";
import { haptic } from "../lib/useTelegram";

type Scheme = "light" | "dark";

const STORAGE_KEY = "abr-out:theme";

function detect(): Scheme {
  const stored = localStorage.getItem(STORAGE_KEY) as Scheme | null;
  if (stored === "light" || stored === "dark") return stored;
  const tg = window.Telegram?.WebApp?.colorScheme;
  if (tg === "light" || tg === "dark") return tg;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function apply(scheme: Scheme) {
  document.documentElement.dataset.scheme = scheme;
}

export function ThemeToggle() {
  const [scheme, setScheme] = useState<Scheme>(() => detect());

  useEffect(() => {
    apply(scheme);
  }, [scheme]);

  function toggle() {
    haptic.selection();
    const next: Scheme = scheme === "dark" ? "light" : "dark";
    localStorage.setItem(STORAGE_KEY, next);
    setScheme(next);
  }

  const isDark = scheme === "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={isDark ? "تم روشن" : "تم تاریک"}
      title={isDark ? "تم روشن" : "تم تاریک"}
    >
      {isDark ? (
        // Sun
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
          <circle cx="12" cy="12" r="4" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="12" y1="2" x2="12" y2="5" />
            <line x1="12" y1="19" x2="12" y2="22" />
            <line x1="2" y1="12" x2="5" y2="12" />
            <line x1="19" y1="12" x2="22" y2="12" />
            <line x1="4.5" y1="4.5" x2="6.6" y2="6.6" />
            <line x1="17.4" y1="17.4" x2="19.5" y2="19.5" />
            <line x1="4.5" y1="19.5" x2="6.6" y2="17.4" />
            <line x1="17.4" y1="6.6" x2="19.5" y2="4.5" />
          </g>
        </svg>
      ) : (
        // Moon
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
          <path
            d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"
            fill="currentColor"
          />
        </svg>
      )}
    </button>
  );
}
