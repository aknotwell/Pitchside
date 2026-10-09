import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `DEV_API_PROXY=https://<api> npm run dev` serves the UI locally against a deployed API
// (the API only allows the Static Web App origin, so the browser must not call it cross-origin).
const target = process.env.DEV_API_PROXY;

export default defineConfig({
  plugins: [react()],
  server: target ? { proxy: { "/api": { target, changeOrigin: true } } } : undefined,
});
