// Presentational brand mark + sidebar icons. No data, network or storage access.
// Geometry follows the approved click-through design (24x24 viewBox, 1.8px round strokes).
import type { ReactElement } from "react";

export type NavIconName = "home" | "customers" | "data" | "coverage" | "assistant" | "diagnostics";

const PATHS: Record<NavIconName, ReactElement> = {
  home: <path d="m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9" />,
  customers: (
    <>
      <circle cx="9" cy="7" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M17 4a3 3 0 0 1 0 6m1 4a5 5 0 0 1 3 4v3" />
    </>
  ),
  data: <path d="M6 3h8l5 5v13H6ZM14 3v6h5M9 13h7M9 17h7" />,
  coverage: <path d="M4 20V11h4v9M10 20V4h4v16M16 20v-7h4v7" />,
  assistant: <path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z" />,
  diagnostics: (
    <>
      <path d="m9 3-1 3-3 1 1 4-3 2 2 3h3l2 5 4-1 1-3 4-1-1-4 3-2-2-3h-3l-2-4Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
};

export function NavIcon({ name }: { name: NavIconName }) {
  return (
    <svg className="moa-nav-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

export function MoaLogo() {
  return (
    <span className="app-brand-mark">
      <svg width="26" height="26" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
        <path fill="#1761ff" d="m2 7 5-4 7 7 7-7 5 4v15l-5 3V12l-7 7-7-7v13l-5-3Z" />
        <path fill="#5a9dff" d="m2 7 5-4v9l-5 4Zm19 5 5-5v15l-5 3Z" />
      </svg>
      <span className="app-brand-name">모아둠</span>
    </span>
  );
}
