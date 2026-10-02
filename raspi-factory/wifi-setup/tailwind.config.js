/** Exactly the config from the Stitch export's inline <script id="tailwind-config">. */
module.exports = {
  darkMode: "class",
  content: ["./*.html"],
  theme: {
    extend: {
      colors: {
        "surface-bright": "#f9f9f9",
        "surface-container-highest": "#e2e2e2",
        "on-tertiary": "#ffffff",
        "on-primary-fixed": "#260059",
        "secondary-fixed": "#ffdad2",
        "secondary": "#af3010",
        "inverse-primary": "#d3bbff",
        "on-tertiary-fixed-variant": "#6e3908",
        "primary": "#340075",
        "on-secondary-fixed": "#3c0700",
        "on-secondary": "#ffffff",
        "primary-fixed": "#ebdcff",
        "surface-container-low": "#f3f3f3",
        "on-secondary-container": "#621100",
        "surface-variant": "#e2e2e2",
        "outline": "#7b7483",
        "secondary-container": "#fe6944",
        "error": "#ba1a1a",
        "secondary-fixed-dim": "#ffb4a2",
        "surface-tint": "#6f46b9",
        "surface-container": "#eeeeee",
        "on-secondary-fixed-variant": "#8a1c00",
        "inverse-surface": "#2f3131",
        "error-container": "#ffdad6",
        "outline-variant": "#ccc3d4",
        "on-surface-variant": "#4a4452",
        "primary-container": "#4c1d95",
        "surface-container-high": "#e8e8e8",
        "surface-container-lowest": "#ffffff",
        "on-tertiary-fixed": "#301400",
        "on-error-container": "#93000a",
        "on-primary-fixed-variant": "#572ba0",
        "primary-fixed-dim": "#d3bbff",
        "tertiary-fixed-dim": "#ffb784",
        "inverse-on-surface": "#f0f1f1",
        "tertiary": "#411d00",
        "surface-dim": "#dadada",
        "on-primary": "#ffffff",
        "tertiary-container": "#622f00",
        "on-error": "#ffffff",
        "on-tertiary-container": "#e19760",
        "on-background": "#1a1c1c",
        "on-primary-container": "#b994ff",
        "tertiary-fixed": "#ffdcc5",
        "surface": "#ffffff",
        "on-surface": "#1a1c1c",
        "background": "#fbfbfe"
      },
      borderRadius: { "DEFAULT": "0.125rem", "lg": "1rem", "xl": "1.5rem", "full": "9999px" },
      spacing: {
        "margin-mobile": "24px", "margin-desktop": "64px", "xs": "8px", "md": "16px",
        "unit": "4px", "max-width-content": "1200px", "lg": "24px", "gutter": "24px",
        "sm": "12px", "xl": "48px"
      },
      fontFamily: {
        "headline-lg": ["Plus Jakarta Sans", "sans-serif"],
        "headline-lg-mobile": ["Plus Jakarta Sans", "sans-serif"],
        "body-md": ["Plus Jakarta Sans", "sans-serif"],
        "label-mono": ["JetBrains Mono", "monospace"],
        "headline-md": ["Plus Jakarta Sans", "sans-serif"],
        "button-text": ["Plus Jakarta Sans", "sans-serif"],
        "body-lg": ["Plus Jakarta Sans", "sans-serif"]
      },
      fontSize: {
        "headline-lg": ["40px", { lineHeight: "1.1", letterSpacing: "-0.03em", fontWeight: "700" }],
        "headline-lg-mobile": ["32px", { lineHeight: "1.15", letterSpacing: "-0.02em", fontWeight: "700" }],
        "body-md": ["16px", { lineHeight: "1.6", fontWeight: "400", letterSpacing: "-0.01em" }],
        "label-mono": ["12px", { lineHeight: "1.5", letterSpacing: "0.08em", fontWeight: "600" }],
        "headline-md": ["24px", { lineHeight: "1.3", fontWeight: "700", letterSpacing: "-0.02em" }],
        "button-text": ["15px", { lineHeight: "1", letterSpacing: "0.01em", fontWeight: "600" }],
        "body-lg": ["18px", { lineHeight: "1.6", fontWeight: "400", letterSpacing: "-0.01em" }]
      },
      boxShadow: {
        "glass": "0 8px 32px 0 rgba(76, 29, 149, 0.05)",
        "glass-hover": "0 12px 48px 0 rgba(76, 29, 149, 0.1)",
        "glass-active": "0 4px 16px 0 rgba(76, 29, 149, 0.05)",
        "nav": "0 -4px 32px 0 rgba(76, 29, 149, 0.05)"
      }
    }
  },
  plugins: [require("@tailwindcss/forms")]
};
