import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
if (
  process.env.VITE_REFERENCE_MODE === "true" &&
  process.env.NODE_ENV === "production"
)
  throw new Error(
    "Reference authentication is prohibited in production builds",
  );
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": process.env.API_PROXY_TARGET || "http://127.0.0.1:8080",
      "/mcp": process.env.API_PROXY_TARGET || "http://127.0.0.1:8080",
    },
  },
  build: { sourcemap: false },
});
