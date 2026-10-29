import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    // 编辑器原子写会生成 .App.tsx.<pid>.tmpdir/ 这类临时目录，
    // Windows 上 chokidar 监听它会 EBUSY 崩溃，直接忽略
    watch: {
      ignored: ["**/.*.tmpdir/**", "**/.*.tmp", "**/.git/**"],
    },
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
