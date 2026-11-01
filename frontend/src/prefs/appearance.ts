// Personal appearance: theme, accent colour, font, text size, corners, density and motion.
// Applied as attributes and CSS variables on <html> (see index.css), saved in this browser for an instant start and
// on the server (/me/preferences) so it follows the person to any device.
export type Theme = "light" | "dark" | "system" | "navy" | "graphite" | "paper" | "contrast";
export type Accent = "blue" | "teal" | "indigo" | "violet" | "emerald" | "rose" | "orange" | "slate";
export type FontKey = "inter" | "poppins" | "roboto" | "open-sans" | "lato" | "nunito" | "dm-sans" | "ibm-plex" | "source-sans" | "lexend" | "atkinson" | "merriweather" | "system";
export type Prefs = {
  theme?: Theme; accent?: Accent; font?: FontKey; text_size?: "s" | "m" | "l" | "xl"; radius?: "rounded" | "soft" | "sharp";
  density?: "comfortable" | "compact"; reduce_motion?: boolean; language?: string; language_name?: string;
};

export const THEMES: { key: Theme; name: string; note: string; dark: boolean; swatch: [string, string, string] }[] = [
  { key: "light", name: "Studio light", note: "Bright and clean (default)", dark: false, swatch: ["#f8fafc", "#ffffff", "#075492"] },
  { key: "dark", name: "Midnight", note: "Dark, easy on the eyes at night", dark: true, swatch: ["#0b1120", "#111827", "#38bdf8"] },
  { key: "navy", name: "Deep ocean", note: "Dark navy, matches the sidebar", dark: true, swatch: ["#061a2f", "#0b2540", "#22d3ee"] },
  { key: "graphite", name: "Graphite", note: "Neutral dark grey", dark: true, swatch: ["#151515", "#1f1f1f", "#a3a3a3"] },
  { key: "paper", name: "Paper", note: "Warm, soft light for long reading", dark: false, swatch: ["#f7f3ea", "#fffdf8", "#9a3412"] },
  { key: "contrast", name: "High contrast", note: "Strong text and borders", dark: false, swatch: ["#ffffff", "#ffffff", "#000000"] },
  { key: "system", name: "Match my device", note: "Light or dark, following your computer", dark: false, swatch: ["#f8fafc", "#111827", "#075492"] },
];

// Tailwind-scale palettes as "r g b" channels, steps 50..900.
const P: Record<Accent, string[]> = {
  blue: ["#f0f7ff", "#dceeff", "#b7dcff", "#80c5ff", "#35a9f4", "#0f8dde", "#086fb8", "#075492", "#083f73", "#062f59"],
  teal: ["#f0fdfa", "#ccfbf1", "#99f6e4", "#5eead4", "#2dd4bf", "#14b8a6", "#0d9488", "#0f766e", "#115e59", "#134e4a"],
  indigo: ["#eef2ff", "#e0e7ff", "#c7d2fe", "#a5b4fc", "#818cf8", "#6366f1", "#4f46e5", "#4338ca", "#3730a3", "#312e81"],
  violet: ["#f5f3ff", "#ede9fe", "#ddd6fe", "#c4b5fd", "#a78bfa", "#8b5cf6", "#7c3aed", "#6d28d9", "#5b21b6", "#4c1d95"],
  emerald: ["#ecfdf5", "#d1fae5", "#a7f3d0", "#6ee7b7", "#34d399", "#10b981", "#059669", "#047857", "#065f46", "#064e3b"],
  rose: ["#fff1f2", "#ffe4e6", "#fecdd3", "#fda4af", "#fb7185", "#f43f5e", "#e11d48", "#be123c", "#9f1239", "#881337"],
  orange: ["#fff7ed", "#ffedd5", "#fed7aa", "#fdba74", "#fb923c", "#f97316", "#ea580c", "#c2410c", "#9a3412", "#7c2d12"],
  slate: ["#f8fafc", "#f1f5f9", "#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569", "#334155", "#1e293b", "#0f172a"],
};
// Button / badge gradient ends: the brand keeps its blue → cyan; other accents run dark → mid of their own hue.
const GRAD: Record<Accent, [string, string]> = {
  blue: ["#1d4ed8", "#0891b2"], teal: ["#0f766e", "#0891b2"], indigo: ["#4338ca", "#6366f1"], violet: ["#6d28d9", "#a855f7"],
  emerald: ["#047857", "#10b981"], rose: ["#be123c", "#f43f5e"], orange: ["#c2410c", "#f59e0b"], slate: ["#334155", "#64748b"],
};
export const ACCENTS: { key: Accent; name: string }[] = [
  { key: "blue", name: "HRP blue" }, { key: "teal", name: "Teal" }, { key: "indigo", name: "Indigo" }, { key: "violet", name: "Violet" },
  { key: "emerald", name: "Emerald" }, { key: "rose", name: "Rose" }, { key: "orange", name: "Saffron" }, { key: "slate", name: "Slate" },
];
export const accentSwatch = (a: Accent) => P[a][6];

export const FONTS: { key: FontKey; name: string; family: string; google?: string; note?: string }[] = [
  { key: "inter", name: "Inter", family: '"Inter Variable", "Inter"', note: "Default" },
  { key: "poppins", name: "Poppins", family: '"Poppins"', google: "Poppins:wght@400;500;600;700" },
  { key: "roboto", name: "Roboto", family: '"Roboto"', google: "Roboto:wght@400;500;700" },
  { key: "open-sans", name: "Open Sans", family: '"Open Sans"', google: "Open+Sans:wght@400;500;600;700" },
  { key: "lato", name: "Lato", family: '"Lato"', google: "Lato:wght@400;700" },
  { key: "nunito", name: "Nunito Sans", family: '"Nunito Sans"', google: "Nunito+Sans:wght@400;600;700" },
  { key: "dm-sans", name: "DM Sans", family: '"DM Sans"', google: "DM+Sans:wght@400;500;700" },
  { key: "ibm-plex", name: "IBM Plex Sans", family: '"IBM Plex Sans"', google: "IBM+Plex+Sans:wght@400;500;600;700" },
  { key: "source-sans", name: "Source Sans 3", family: '"Source Sans 3"', google: "Source+Sans+3:wght@400;600;700" },
  { key: "lexend", name: "Lexend", family: '"Lexend"', google: "Lexend:wght@400;500;600;700", note: "Easy to read" },
  { key: "atkinson", name: "Atkinson Hyperlegible", family: '"Atkinson Hyperlegible"', google: "Atkinson+Hyperlegible:wght@400;700", note: "For low vision" },
  { key: "merriweather", name: "Merriweather", family: '"Merriweather"', google: "Merriweather:wght@400;700", note: "Serif" },
  { key: "system", name: "Your device's font", family: "system-ui, -apple-system, \"Segoe UI\"" },
];
export const TEXT_SIZES = [{ key: "s", name: "Small", scale: 0.9375 }, { key: "m", name: "Default", scale: 1 }, { key: "l", name: "Large", scale: 1.0938 }, { key: "xl", name: "Extra large", scale: 1.1875 }] as const;

const rgb = (hex: string) => `${parseInt(hex.slice(1, 3), 16)} ${parseInt(hex.slice(3, 5), 16)} ${parseInt(hex.slice(5, 7), 16)}`;
const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900];
const KEY = "dm.prefs";

export function loadLocal(): Prefs {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}") as Prefs; } catch { return {}; }
}
export function saveLocal(p: Prefs) {
  try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* private mode */ }
}

export function loadFont(family: string, google?: string) {
  if (!google) return;
  const id = `gf-${google.split(":")[0]}`;
  if (document.getElementById(id)) return;
  const l = document.createElement("link");
  l.id = id; l.rel = "stylesheet";
  l.href = `https://fonts.googleapis.com/css2?family=${google}&display=swap`;
  document.head.appendChild(l);
  void family;
}

let mq: MediaQueryList | null = null;
export function isDark(p: Prefs) {
  const t = p.theme ?? "light";
  if (t === "system") return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return THEMES.find((x) => x.key === t)?.dark ?? false;
}

/** Apply everything except the language (see i18n.ts). Safe to call often. */
export function applyAppearance(p: Prefs, scriptFont = "") {
  const root = document.documentElement;
  const theme = p.theme ?? "light";
  const resolved = theme === "system" ? (isDark(p) ? "dark" : "light") : theme;
  root.dataset.theme = resolved;
  root.dataset.mode = isDark(p) ? "dark" : "light";
  root.style.colorScheme = isDark(p) ? "dark" : "light";
  const acc = p.accent ?? "blue";
  P[acc].forEach((hex, i) => root.style.setProperty(`--p-${STEPS[i]}`, rgb(hex)));
  root.style.setProperty("--g1", rgb(GRAD[acc][0]));
  root.style.setProperty("--g2", rgb(GRAD[acc][1]));
  const f = FONTS.find((x) => x.key === (p.font ?? "inter")) ?? FONTS[0];
  loadFont(f.family, f.google);
  root.style.setProperty("--font-sans", `${f.family}, ${scriptFont ? `${scriptFont}, ` : ""}"Inter Variable", system-ui, sans-serif`);
  const size = TEXT_SIZES.find((s) => s.key === (p.text_size ?? "m")) ?? TEXT_SIZES[1];
  root.style.fontSize = `${16 * size.scale}px`;
  root.dataset.radius = p.radius ?? "rounded";
  root.dataset.density = p.density ?? "comfortable";
  root.dataset.motion = p.reduce_motion ? "reduce" : "full";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", isDark(p) ? "#0b1120" : "#062f59");
  if (theme === "system" && !mq && typeof window !== "undefined") {
    mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", () => applyAppearance(loadLocal(), scriptFont));
  }
}
