import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Theme = "dark" | "light";
const KEY = "inspiration_theme";

const ThemeContext = createContext<{ theme: Theme; toggle: () => void } | null>(null);

function apply(theme: Theme) {
  const el = document.documentElement;
  el.classList.remove("dark", "light");
  el.classList.add(theme);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem(KEY) as Theme) || "dark");
  useEffect(() => {
    apply(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);
  return (
    <ThemeContext.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
