import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api/sunucu-hazir": {
          target: "http://127.0.0.1:8189",
          changeOrigin: true,
        },
        "/api/setup-state": {
          target: "http://127.0.0.1:8192",
          changeOrigin: true,
        },
      },
    },
  };
});
