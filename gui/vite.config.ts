import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port; `ombro serve --dev` accepts this origin.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true, host: "localhost" },
  build: { target: "safari15", outDir: "dist" },
});
