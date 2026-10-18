import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files are served by the FastAPI webhook at /dashboard/.
// In dev (npm run dev), API calls are proxied to the webhook on port 8000.
export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  server: {
    port: 5173,
    proxy: { "/v1": "http://127.0.0.1:8000" },
  },
});
