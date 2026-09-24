/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter Variable", "ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--border))",
        ring: "hsl(var(--accent))",
        background: "hsl(var(--bg))",
        foreground: "hsl(var(--text))",
        card: "hsl(var(--surface))",
        "card-foreground": "hsl(var(--text))",
        popover: "hsl(var(--surface))",
        "popover-foreground": "hsl(var(--text))",
        primary: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(0 0% 100%)",
        },
        secondary: {
          DEFAULT: "hsl(var(--surface-2))",
          foreground: "hsl(var(--text))",
        },
        muted: {
          DEFAULT: "hsl(var(--surface-2))",
          foreground: "hsl(var(--text-muted))",
        },
        accent: {
          DEFAULT: "hsl(var(--surface-2))",
          foreground: "hsl(var(--text))",
        },
        destructive: {
          DEFAULT: "hsl(var(--estado-critico))",
          foreground: "hsl(0 0% 100%)",
        },
        // Estados del tráfico: siempre color + icono + texto (nunca solo color).
        "estado-en-ruta": "hsl(var(--estado-en-ruta))",
        "estado-libre": "hsl(var(--estado-libre))",
        "estado-aviso": "hsl(var(--estado-aviso))",
        "estado-critico": "hsl(var(--estado-critico))",
        "estado-parado": "hsl(var(--estado-parado))",
      },
      borderRadius: {
        lg: "10px",
        md: "6px",
        sm: "4px",
      },
      fontSize: {
        // Densidad: 13px en tablas/paneles, 14px en formularios.
        base: ["14px", { lineHeight: "20px" }],
        "2xs": ["13px", { lineHeight: "16px" }],
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
