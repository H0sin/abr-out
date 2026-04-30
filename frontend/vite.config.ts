import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Mini App is served at https://<domain>/app/, so all assets need /app/ prefix.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
