import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { App } from "./App";
import { Browse } from "./pages/Browse";
import { MyConfigs } from "./pages/MyConfigs";
import { Sell } from "./pages/Sell";
import { Wallet } from "./pages/Wallet";
import "./styles.css";

window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();

// Telegram appends "#tgWebAppData=...&tgWebAppVersion=..." to the URL.
// HashRouter would treat that whole blob as a route and fail to match.
// Strip the Telegram hash so HashRouter starts fresh at "#/".
if (window.location.hash.startsWith("#tgWebApp")) {
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

// Mobile debug overlay: surface uncaught errors on screen since we can't
// open DevTools inside the Telegram mobile webview.
function showError(label: string, message: string) {
  const id = "__err_overlay__";
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement("div");
    el.id = id;
    el.style.cssText =
      "position:fixed;left:0;right:0;bottom:0;max-height:50vh;overflow:auto;" +
      "background:#b00020;color:#fff;font:12px/1.4 monospace;padding:8px;" +
      "z-index:99999;white-space:pre-wrap;direction:ltr;";
    document.body.appendChild(el);
  }
  el.textContent += `[${label}] ${message}\n`;
}
window.addEventListener("error", (e) => {
  showError("error", `${e.message} @ ${e.filename}:${e.lineno}`);
});
window.addEventListener("unhandledrejection", (e) => {
  showError("promise", String(e.reason?.stack || e.reason));
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<Navigate to="/browse" replace />} />
          <Route path="browse" element={<Browse />} />
          <Route path="my" element={<MyConfigs />} />
          <Route path="sell" element={<Sell />} />
          <Route path="wallet" element={<Wallet />} />
        </Route>
      </Routes>
    </HashRouter>
  </React.StrictMode>,
);
