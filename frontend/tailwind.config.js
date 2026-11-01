/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // hrpai.in palette
        primary: Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900].map((k) => [k, `rgb(var(--p-${k}) / <alpha-value>)`])),
        grad1: "rgb(var(--g1) / <alpha-value>)", grad2: "rgb(var(--g2) / <alpha-value>)",
        secondary: { 50: "#ecfeff", 100: "#cffafe", 200: "#a5f3fc", 300: "#67e8f9", 400: "#22d3ee", 500: "#06a6c7", 600: "#0787a4", 700: "#0b6d86", 800: "#12576d", 900: "#15475a" },
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        brand: { DEFAULT: "rgb(var(--brand) / <alpha-value>)", deep: "rgb(var(--brand-deep) / <alpha-value>)", tint: "rgb(var(--brand-tint) / <alpha-value>)" },
        accent: { DEFAULT: "rgb(var(--accent) / <alpha-value>)", tint: "rgb(var(--accent-tint) / <alpha-value>)" },
        danger: { DEFAULT: "rgb(var(--danger) / <alpha-value>)", tint: "rgb(var(--danger-tint) / <alpha-value>)" },
      },
      fontFamily: {
        display: ["var(--font-sans)"],
        sans: ["var(--font-sans)"],
      },
    },
  },
  plugins: [],
};
