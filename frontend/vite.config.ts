import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: backend runs at localhost:8000 (FastAPI). Prod: same origin (FastAPI serves dist/).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
