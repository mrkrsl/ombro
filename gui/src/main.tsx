import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
// The voice of the Lead and its crews: Zilla Slab, regular and semibold, with italics.
import "@fontsource/zilla-slab/400.css";
import "@fontsource/zilla-slab/400-italic.css";
import "@fontsource/zilla-slab/600.css";
import "@fontsource/zilla-slab/600-italic.css";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./tokens.css";
import "./app.css";
import { keptThemeChoice, themeFor } from "./store/store";

// The theme you chose last, on the page before anything is drawn.
document.documentElement.dataset.theme = themeFor(keptThemeChoice());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
