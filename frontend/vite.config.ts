import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The backend serves /api. Override with VITE_API_TARGET (e.g. agents use :8001).
const target = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    strictPort: true,
    proxy: {
      "/api": { target, changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
