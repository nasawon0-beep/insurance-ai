import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Theme = "light" | "dark" | "system";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  actualTheme: "light" | "dark"; // 실제 적용된 테마 (system 해소 후)
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem("insurance-ai-theme");
    return (saved as Theme) || "system";
  });

  const [actualTheme, setActualTheme] = useState<"light" | "dark">("light");

  // 시스템 테마 감지
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    
    const updateActualTheme = () => {
      if (theme === "system") {
        setActualTheme(mediaQuery.matches ? "dark" : "light");
      } else {
        setActualTheme(theme);
      }
    };

    updateActualTheme();

    // 시스템 테마 변경 감지
    const listener = (e: MediaQueryListEvent) => {
      if (theme === "system") {
        setActualTheme(e.matches ? "dark" : "light");
      }
    };

    mediaQuery.addEventListener("change", listener);
    return () => mediaQuery.removeEventListener("change", listener);
  }, [theme]);

  // HTML에 data-theme 적용
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", actualTheme);
  }, [actualTheme]);

  const setTheme = (newTheme: Theme) => {
    setThemeState(newTheme);
    localStorage.setItem("insurance-ai-theme", newTheme);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, actualTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return context;
}
