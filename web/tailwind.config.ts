import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0d1117",
        panel: "#161b22",
        panel2: "#1c2230",
        border: "#2d333b",
        ink: "#e6edf3",
        muted: "#8b949e",
        accent: "#4493f8",
        ok: "#3fb950",
        warn: "#d29922",
        bad: "#f85149",
      },
      borderRadius: { xl: "12px" },
      fontFamily: {
        sans: ["var(--font-sans)", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
