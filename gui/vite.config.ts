import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port; `ombro serve --dev` accepts this origin.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true, host: "localhost" },
  build: { target: "safari15", outDir: "dist" },
  test: { environment: "jsdom", setupFiles: ["src/test-setup.ts"] },
});
