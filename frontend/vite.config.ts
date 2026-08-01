import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production assets are served by FastAPI's StaticFiles mount at /static, so
// built asset URLs must be absolute under /static/dist/ while the SPA itself is
// served at /. In dev we serve from the root for a normal localhost URL.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "serve" ? "/" : "/static/dist/",
  build: {
    outDir: "../app/static/dist",
    emptyOutDir: true,
    // recharts is code-split away by the lazy import in Dashboard.tsx
    chunkSizeWarningLimit: 600,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8600",
    },
  },
}));
