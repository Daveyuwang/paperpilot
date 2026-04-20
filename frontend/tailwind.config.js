/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Light surfaces
        surface: {
          0:   "#ffffff",
          50:  "#faf9f7",   // warm off-white — primary bg
          100: "#f4f2ee",   // slightly deeper panel
          200: "#ede9e3",   // border / divider
          300: "#ddd8d0",   // stronger border
          400: "#b8b0a4",   // muted text / placeholder
          500: "#8a8278",   // secondary text
          600: "#5c564f",   // body text
          700: "#3d3830",   // strong text
          800: "#2a2520",   // near-black
          900: "#1a1714",   // deepest
        },
        accent: {
          50:  "#f0f4ff",
          100: "#e0eaff",
          200: "#c2d4ff",
          300: "#93b4fd",
          400: "#6090f8",
          500: "#3d6ff0",
          600: "#2952d9",
          700: "#1e3fad",
        },
      },
      fontFamily: {
        sans:  ["Inter", "system-ui", "sans-serif"],
        serif: ["'Lora'", "Georgia", "Cambria", "serif"],
        mono:  ["JetBrains Mono", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderColor: {
        DEFAULT: "#ede9e3",
      },
    },
  },
  plugins: [],
  darkMode: "class",
};
