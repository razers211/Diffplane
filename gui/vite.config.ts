import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served by the orchestrator under /ui/. In development, API calls go to the local orchestrator,
// which serves HTTPS only with a self-signed certificate (orchestrator/tls.py).
const api = { target: "https://127.0.0.1:8000", secure: false };

export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: {
    proxy: {
      "/devices": api,
      "/topology": api,
      "/health": api,
      "/diff-jobs": api,
      "/deploy-jobs": api,
      "/auth": api,
      "/audit": api,
      "/segments": api,
      "/services": api,
      "/drift": api,
    },
  },
});
