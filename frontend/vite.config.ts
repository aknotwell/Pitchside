import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, /ws is proxied to the live server started with `make live`.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/ws": { target: "ws://127.0.0.1:8000", ws: true } },
  },
});
