/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // hrpai.in palette
        primary: { 50: "#f0f7ff", 100: "#dceeff", 200: "#b7dcff", 300: "#80c5ff", 400: "#35a9f4", 500: "#0f8dde", 600: "#086fb8", 700: "#075492", 800: "#083f73", 900: "#062f59" },
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
        display: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"],
        sans: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
