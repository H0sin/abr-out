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
