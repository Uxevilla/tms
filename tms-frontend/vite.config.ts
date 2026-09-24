import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy compartido: /api/ y /ws/ al backend local (dev y preview, sin nginx).
const proxy = {
  "/api": "http://localhost:8750",
  "/ws": { target: "ws://localhost:8750", ws: true },
};

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": "/src",
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy,
  },
  preview: {
    host: true,
    port: 8080,
    proxy,
  },
});
