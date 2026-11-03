import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // For `npm run dev` against a local api. In compose, nginx does this instead.
  server: { proxy: { "/v1": "http://localhost:8000" } },
});
