/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", ".dark"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--bg))",
        surface: "hsl(var(--surface))",
        card: "hsl(var(--card))",
        elevated: "hsl(var(--elevated))",
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        foreground: "hsl(var(--fg))",
        background: "hsl(var(--bg))",
        faint: "hsl(var(--faint))",
        muted: { DEFAULT: "hsl(var(--surface))", foreground: "hsl(var(--muted))" },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          strong: "hsl(var(--primary-strong))",
          foreground: "hsl(var(--primary-fg))",
        },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-fg))" },
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        danger: "hsl(var(--danger))",
        info: "hsl(var(--info))",
        ring: "hsl(var(--ring))",
      },
      boxShadow: {
        panel: "0 2px 8px rgb(0 0 0 / 0.08)",
      },
      borderRadius: {
        xl: "14px",
        lg: "12px",
        md: "8px",
        sm: "6px",
      },
      transitionDuration: {
        DEFAULT: "150ms",
      },
    },
  },
  plugins: [],
};
