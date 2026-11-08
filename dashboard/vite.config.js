import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files are served by the FastAPI webhook at /dashboard/, or by Vercel at the site root
// (Vercel sets VERCEL=1 during its build).
// In dev (npm run dev), API calls are proxied to the webhook on port 8000.
export default defineConfig({
  plugins: [react()],
  base: process.env.VERCEL ? "/" : "/dashboard/",
  server: {
    port: 5173,
    proxy: { "/v1": "http://127.0.0.1:8000" },
  },
});
