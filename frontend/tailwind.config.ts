import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#f5f3ee",
        surface: "#ffffff",
        ink: "#14181f",
        muted: "#667085",
        line: "#e4e0d6",
        side: "#0f1318",
        brand: "#e2542b",
        good: "#19704a",
        warn: "#a8660b",
        bad: "#b7352a",
        info: "#2b56a0",
        "good-bg": "#e6f3ec",
        "warn-bg": "#fbf0dc",
        "bad-bg": "#fbe7e4",
        "info-bg": "#e5edf9",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: { card: "0 1px 0 rgba(20,24,31,0.04), 0 1px 2px rgba(20,24,31,0.05)" },
    },
  },
  plugins: [],
};
export default config;
