import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthGate } from "./auth";
import { Provider } from "./ctx";
import { isApp } from "./api";
import ErrorBoundary from "./components/ErrorBoundary";
import { installGlobalErrorReporting } from "./errors";
import { registerServiceWorker } from "./pwa";
import "./theme.css";
import "./firstrun.css";
import "./compact.css";

// A computer opening the site lands in Studio (the power tools). Phones, tablets, the installed home-screen app and
// the Android app open the phone view. Only the first page load decides; Studio's "Phone view" link still works.
const desktop = window.matchMedia("(min-width: 900px) and (pointer: fine)").matches;
const installed = window.matchMedia("(display-mode: standalone)").matches;
const forcedTablet = /[?&]layout=tablet(&|$)/.test(location.search) || (() => { try { return localStorage.getItem("et-layout") === "tablet"; } catch { return false; } })();
if (location.pathname === "/" && desktop && !installed && !isApp() && !forcedTablet) history.replaceState(null, "", "/studio" + location.search + location.hash);

installGlobalErrorReporting();
registerServiceWorker();          // Web Push for the browser / iPhone Home Screen app (not inside the Android app)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary full name="app">
    <BrowserRouter>
      <AuthGate>
        <Provider>
          <App />
        </Provider>
      </AuthGate>
    </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
