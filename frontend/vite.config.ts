import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      // 前端 /api 代理到后端,避免跨域
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
