import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { App } from "./App";
import { Browse } from "./pages/Browse";
import { MyConfigs } from "./pages/MyConfigs";
import { Sell } from "./pages/Sell";
import { Wallet } from "./pages/Wallet";
import { Withdraw } from "./pages/Withdraw";
import { AdminLayout } from "./pages/admin/AdminLayout";
import { AdminHome } from "./pages/admin/Home";
import { AdminUsers } from "./pages/admin/Users";
import { AdminUserDetail } from "./pages/admin/UserDetail";
import { AdminUserTransactions } from "./pages/admin/UserTransactions";
import { AdminBroadcast } from "./pages/admin/Broadcast";
import { AdminSupport } from "./pages/admin/Support";
import { AdminWithdrawalWallet } from "./pages/admin/WithdrawalWallet";
import { ToastProvider } from "./lib/toast";
import { MeProvider } from "./lib/MeContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles.css";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

// If Telegram did not provide initData, show a clear message instead of a
// silent 401 from the API. This catches: opening the URL in a normal browser,
// using a stale/cached page, or an old Telegram client that does not pass
// initData to the WebApp.
const hasInitData = Boolean(tg?.initData);
if (!hasInitData) {
  document.getElementById("root")!.innerHTML = `
    <div style="padding:24px;font:14px/1.7 Vazirmatn,system-ui,sans-serif;
                color:var(--tg-theme-text-color,#111);text-align:center;">
      <h2 style="margin:0 0 12px">دسترسی فقط از طریق ربات</h2>
      <p>این صفحه باید از داخل ربات تلگرام و با دکمهٔ «🚀 باز کردن مینی‌اپ» باز شود.</p>
      <p style="opacity:.7;font-size:12px;margin-top:24px">
        debug: tg=${tg ? "yes" : "no"}, initData=${tg?.initData?.length ?? 0} chars,
        version=${tg?.version ?? "?"}, platform=${tg?.platform ?? "?"}
      </p>
    </div>`;
  throw new Error("missing Telegram WebApp initData");
}

// Telegram appends "#tgWebAppData=...&tgWebAppVersion=..." to the URL.
// HashRouter would treat that whole blob as a route and fail to match.
if (
  window.location.hash.startsWith("#tgWebApp") ||
  window.location.search.includes("tgWebAppStartParam")
) {
  history.replaceState(null, "", window.location.pathname);
}

// Apply the Telegram theme to <meta theme-color> + html background and
// re-apply whenever the user flips light/dark in Telegram settings. A user
// override stored in localStorage (set via the ThemeToggle) wins over both
// Telegram and OS preferences.
function applyTheme() {
  const t = window.Telegram?.WebApp;
  const stored = localStorage.getItem("abr-out:theme");
  const scheme =
    stored === "dark" || stored === "light"
      ? stored
      : t?.colorScheme ??
        (window.matchMedia?.("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light");
  document.documentElement.dataset.scheme = scheme;
  if (!t) return;
  const bg =
    t.themeParams?.secondary_bg_color ||
    t.themeParams?.bg_color ||
    (scheme === "dark" ? "#0b1220" : "#f5f6fa");
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
window
  .matchMedia?.("(prefers-color-scheme: dark)")
  .addEventListener?.("change", applyTheme);

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
                <Route path="withdraw" element={<Withdraw />} />
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
                  <Route path="wallet" element={<AdminWithdrawalWallet />} />
                </Route>
              </Route>
            </Routes>
          </HashRouter>
        </MeProvider>
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
