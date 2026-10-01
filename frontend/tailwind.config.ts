import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "paper": "rgb(var(--c-paper) / <alpha-value>)",
        "surface": "rgb(var(--c-surface) / <alpha-value>)",
        "ink": "rgb(var(--c-ink) / <alpha-value>)",
        "muted": "rgb(var(--c-muted) / <alpha-value>)",
        "line": "rgb(var(--c-line) / <alpha-value>)",
        "side": "rgb(var(--c-side) / <alpha-value>)",
        "brand": "rgb(var(--c-brand) / <alpha-value>)",
        "accent": "rgb(var(--c-accent) / <alpha-value>)",
        "on-accent": "rgb(var(--c-on-accent) / <alpha-value>)",
        "good": "rgb(var(--c-good) / <alpha-value>)",
        "warn": "rgb(var(--c-warn) / <alpha-value>)",
        "bad": "rgb(var(--c-bad) / <alpha-value>)",
        "info": "rgb(var(--c-info) / <alpha-value>)",
        "good-bg": "rgb(var(--c-good-bg) / <alpha-value>)",
        "warn-bg": "rgb(var(--c-warn-bg) / <alpha-value>)",
        "bad-bg": "rgb(var(--c-bad-bg) / <alpha-value>)",
        "info-bg": "rgb(var(--c-info-bg) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: { card: "0 0 0 1px rgba(34,211,238,0.03), 0 8px 24px -12px rgba(0,0,0,0.6)", glow: "0 0 0 1px rgba(34,211,238,0.35), 0 0 24px -4px rgba(34,211,238,0.35)" },
    },
  },
  plugins: [],
};
export default config;
