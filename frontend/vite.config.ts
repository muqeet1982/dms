import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/ext": { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500, rollupOptions: { output: { manualChunks: { react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query", "zustand"], charts: ["apexcharts", "react-apexcharts"], scan: ["@zxing/browser"] } } } },
});
