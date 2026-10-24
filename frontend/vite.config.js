import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard talks to two services on this computer through the dev server:
//   /api        -> the SimGuard backend   (127.0.0.1:8000)
//   /connector  -> the local connector    (127.0.0.1:8001), which reads granted folders
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", rewrite: (path) => path.replace(/^\/api/, "") },
      "/connector": { target: "http://127.0.0.1:8001", rewrite: (path) => path.replace(/^\/connector/, "") },
    },
  },
});
