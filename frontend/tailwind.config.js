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
      backgroundImage: {
        // 琥珀金主操作渐变(CTA / 主按钮)
        "gradient-primary": "linear-gradient(135deg, hsl(var(--primary)), hsl(var(--primary-strong)))",
        // 金→青进度/装饰渐变
        "gradient-accent": "linear-gradient(90deg, hsl(var(--primary)), hsl(var(--accent)))",
      },
      boxShadow: {
        // 主强调外发光(聚光灯感)
        glow: "0 0 18px -4px hsl(var(--primary-glow) / 0.7)",
        "glow-sm": "0 0 12px -4px hsl(var(--primary-glow) / 0.6)",
        panel: "0 8px 30px -12px rgba(0, 0, 0, 0.7)",
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
