import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Ports come from .env via start.ps1 (API_PORT / WEB_PORT).
const apiPort = process.env.API_PORT || "8600";
const webPort = Number(process.env.WEB_PORT || 5180);

export default defineConfig({
  plugins: [react()],
  server: {
    port: webPort,
    strictPort: true,
    proxy: { "/api": `http://127.0.0.1:${apiPort}` },
  },
});
