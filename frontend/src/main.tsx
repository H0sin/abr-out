import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { App } from "./App";
import { Browse } from "./pages/Browse";
import { MyConfigs } from "./pages/MyConfigs";
import { Sell } from "./pages/Sell";
import { Wallet } from "./pages/Wallet";
import { AdminLayout } from "./pages/admin/AdminLayout";
import { AdminHome } from "./pages/admin/Home";
import { AdminUsers } from "./pages/admin/Users";
import { AdminUserDetail } from "./pages/admin/UserDetail";
import { AdminUserTransactions } from "./pages/admin/UserTransactions";
import { AdminBroadcast } from "./pages/admin/Broadcast";
import { AdminSupport } from "./pages/admin/Support";
import { ToastProvider } from "./lib/toast";
import { MeProvider } from "./lib/MeContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles.css";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

// Telegram appends "#tgWebAppData=...&tgWebAppVersion=..." to the URL.
// HashRouter would treat that whole blob as a route and fail to match.
if (
  window.location.hash.startsWith("#tgWebApp") ||
  window.location.search.includes("tgWebAppStartParam")
) {
  history.replaceState(null, "", window.location.pathname);
}

// Apply the Telegram theme to <meta theme-color> + html background and
// re-apply whenever the user flips light/dark in Telegram settings.
function applyTheme() {
  const t = window.Telegram?.WebApp;
  if (!t) return;
  const bg =
    t.themeParams?.secondary_bg_color ||
    t.themeParams?.bg_color ||
    (t.colorScheme === "dark" ? "#0b1220" : "#f5f6fa");
  document.documentElement.dataset.scheme = t.colorScheme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", bg);
  try {
    t.setHeaderColor("secondary_bg_color");
    t.setBackgroundColor("secondary_bg_color");
  } catch {
    /* older clients */
  }
}
applyTheme();
tg?.onEvent("themeChanged", applyTheme);

// Dev-only error overlay (kept off in production builds).
if (import.meta.env.DEV) {
  const showError = (label: string, message: string) => {
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
  };
  window.addEventListener("error", (e) =>
    showError("error", `${e.message} @ ${e.filename}:${e.lineno}`),
  );
  window.addEventListener("unhandledrejection", (e) =>
    showError("promise", String(e.reason?.stack || e.reason)),
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ToastProvider>
        <MeProvider>
          <HashRouter>
            <Routes>
              <Route path="/" element={<App />}>
                <Route index element={<Navigate to="/browse" replace />} />
                <Route path="browse" element={<Browse />} />
                <Route path="my" element={<MyConfigs />} />
                <Route path="sell" element={<Sell />} />
                <Route path="wallet" element={<Wallet />} />
                <Route path="admin" element={<AdminLayout />}>
                  <Route index element={<AdminHome />} />
                  <Route path="users" element={<AdminUsers />} />
                  <Route path="users/:id" element={<AdminUserDetail />} />
                  <Route
                    path="users/:id/transactions"
                    element={<AdminUserTransactions />}
                  />
                  <Route path="broadcast" element={<AdminBroadcast />} />
                  <Route path="support" element={<AdminSupport />} />
                </Route>
              </Route>
            </Routes>
          </HashRouter>
        </MeProvider>
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
