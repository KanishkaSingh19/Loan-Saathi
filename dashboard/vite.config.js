import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files are served by the FastAPI webhook at /dashboard/, or as a static site at the site root:
// Vercel (sets VERCEL=1), Netlify (sets NETLIFY=true) or any other host with STATIC_SITE=1.
// A static site calls the API at VITE_API_BASE (see deploy/DEPLOY.md).
// In dev (npm run dev), API calls are proxied to the webhook on port 8000.
export default defineConfig({
  plugins: [react()],
  base: process.env.VERCEL || process.env.NETLIFY || process.env.STATIC_SITE ? "/" : "/dashboard/",
  server: {
    port: 5173,
    proxy: { "/v1": "http://127.0.0.1:8000" },
  },
});
