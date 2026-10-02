import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5183,
    strictPort: true,
    watch: { ignored: ["**/test-results*/**", "**/playwright-report/**"] },
    proxy: {
      "/api": { target: "http://127.0.0.1:8087", changeOrigin: false },
      "/admin": { target: "http://127.0.0.1:8087", changeOrigin: false },
      "/static": { target: "http://127.0.0.1:8087", changeOrigin: false },
      "/media": { target: "http://127.0.0.1:8087", changeOrigin: false },
    },
  },
});
