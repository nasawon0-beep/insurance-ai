import { useTheme } from "../ThemeContext";

export function ThemeToggle() {
  const { setTheme, actualTheme } = useTheme();

  const handleToggle = () => {
    // 현재 실제 테마에 따라 토글
    if (actualTheme === "light") {
      setTheme("dark");
    } else {
      setTheme("light");
    }
  };

  return (
    <button
      onClick={handleToggle}
      className="theme-toggle"
      aria-label={`현재 ${actualTheme === "dark" ? "다크" : "라이트"} 모드, 클릭하여 전환`}
      title={`${actualTheme === "dark" ? "라이트" : "다크"} 모드로 전환`}
    >
      {actualTheme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}

export function ThemeSelector() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="theme-selector">
      <label htmlFor="theme-select" style={{ fontSize: "var(--text-sm)", fontWeight: "var(--font-medium)" }}>
        테마 설정
      </label>
      <select
        id="theme-select"
        value={theme}
        onChange={(e) => setTheme(e.target.value as "light" | "dark" | "system")}
        style={{
          marginTop: "var(--space-2)",
          padding: "var(--space-2) var(--space-3)",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border-default)",
          backgroundColor: "var(--color-bg-surface)",
          color: "var(--color-text-primary)",
          fontSize: "var(--text-sm)",
          cursor: "pointer",
        }}
      >
        <option value="light">라이트 모드</option>
        <option value="dark">다크 모드</option>
        <option value="system">시스템 설정 따르기</option>
      </select>
      <p style={{ 
        fontSize: "var(--text-xs)", 
        color: "var(--color-text-secondary)", 
        marginTop: "var(--space-2)" 
      }}>
        {theme === "system" 
          ? "현재 시스템 설정에 따라 자동으로 전환됩니다" 
          : `${theme === "dark" ? "다크" : "라이트"} 모드가 적용되었습니다`}
      </p>
    </div>
  );
}
