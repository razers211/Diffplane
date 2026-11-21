import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// Bundled fonts, served by the orchestrator with the GUI; nothing is fetched from the internet.
// Archivo carries its width axis (labels and figures are set condensed); each subset loads only
// when a page uses its characters.
import "@fontsource-variable/archivo/wdth.css";
import "@fontsource-variable/red-hat-mono/wght.css";
import "./styles.css";

// The theme chosen with the top bar's button, set before the first paint.
try {
  const theme = localStorage.getItem("theme");
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
} catch { /* storage blocked: follow the system */ }

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
