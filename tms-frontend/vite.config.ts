import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Proxy para desarrollo sin nginx: /api/ y /ws/ al backend local.
    proxy: {
      "/api": "http://localhost:8750",
      "/ws": { target: "ws://localhost:8750", ws: true },
    },
  },
  preview: {
    host: true,
    port: 8080,
  },
});
