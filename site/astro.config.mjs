// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// GitHub Pages serves the site at https://harshaksh-singh.github.io/rubberstamp/
export default defineConfig({
  site: "https://harshaksh-singh.github.io",
  base: "/rubberstamp",
  trailingSlash: "ignore",
  vite: { plugins: [tailwindcss()] },
});
