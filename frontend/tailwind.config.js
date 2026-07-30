/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Restrained, cool-neutral product surfaces. Values include an alpha
        // placeholder so Tailwind opacity modifiers continue to work.
        surface: {
          0:   "oklch(100% 0 0 / <alpha-value>)",
          50:  "oklch(98.2% 0.004 255 / <alpha-value>)",
          100: "oklch(96.2% 0.006 255 / <alpha-value>)",
          200: "oklch(91.5% 0.009 255 / <alpha-value>)",
          300: "oklch(78% 0.014 255 / <alpha-value>)",
          400: "oklch(52% 0.018 255 / <alpha-value>)",
          500: "oklch(43% 0.018 255 / <alpha-value>)",
          600: "oklch(34% 0.016 255 / <alpha-value>)",
          700: "oklch(27% 0.014 255 / <alpha-value>)",
          800: "oklch(21% 0.012 255 / <alpha-value>)",
          900: "oklch(15% 0.01 255 / <alpha-value>)",
        },
        accent: {
          50:  "oklch(97% 0.018 260 / <alpha-value>)",
          100: "oklch(93.5% 0.038 260 / <alpha-value>)",
          200: "oklch(87% 0.072 260 / <alpha-value>)",
          300: "oklch(77% 0.12 260 / <alpha-value>)",
          400: "oklch(66% 0.17 260 / <alpha-value>)",
          500: "oklch(57% 0.2 260 / <alpha-value>)",
          600: "oklch(49% 0.2 260 / <alpha-value>)",
          700: "oklch(41% 0.17 260 / <alpha-value>)",
        },
      },
      fontFamily: {
        sans:  ["ui-sans-serif", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        serif: ["'Lora'", "Georgia", "Cambria", "serif"],
        mono:  ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderColor: {
        DEFAULT: "oklch(91.5% 0.009 255)",
      },
    },
  },
  plugins: [],
  darkMode: "class",
};
