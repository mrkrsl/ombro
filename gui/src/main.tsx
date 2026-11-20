import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
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
