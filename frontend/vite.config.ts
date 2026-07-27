import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on 5173 (the origin the API allows via CORS). In production the
// built assets are served same-origin by the API image (Phase 8).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
