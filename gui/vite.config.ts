import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port; `ombro serve --dev` accepts this origin.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true, host: "localhost" },
  build: { target: "safari15", outDir: "dist" },
  // css: app.css as text (?raw) for the test of the Markdown heading scale; it is not applied to pages.
  test: { environment: "jsdom", setupFiles: ["src/test-setup.ts"], css: { include: [/app\.css/] } },
});
