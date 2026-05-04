import { Skeleton } from "../components/ui";
import { TransactionsList } from "../components/TransactionsList";
import { useMe } from "../lib/MeContext";
import { haptic } from "../lib/useTelegram";
import { Link } from "react-router-dom";

function formatUsd(raw: string): string {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return raw;
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  });
}

function initial(name?: string | null, fallback?: string): string {
  const s = (name || fallback || "").trim();
  if (!s) return "?";
  return s[0]!.toUpperCase();
}

export function Wallet() {
  const { me, loading, error, refresh } = useMe();
  const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user;

  function close() {
    haptic.light();
    window.Telegram?.WebApp?.close();
  }

  return (
    <div>
      {error && (
        <div className="alert alert-error mt-2">
          {error}{" "}
          <button
            onClick={refresh}
            className="btn-ghost"
            style={{
              background: "none",
              border: "none",
              padding: 0,
              color: "inherit",
              textDecoration: "underline",
            }}
          >
            تلاش مجدد
          </button>
        </div>
      )}

      {loading && !me && (
        <>
          <div className="balance-hero">
            <Skeleton width="40%" height={14} style={{ margin: "0 auto" }} />
            <div style={{ height: 12 }} />
            <Skeleton
              width="50%"
              height={40}
              style={{ margin: "0 auto", background: "rgba(255,255,255,0.25)" }}
            />
            <div style={{ height: 12 }} />
            <Skeleton width="30%" height={12} style={{ margin: "0 auto" }} />
          </div>
        </>
      )}

      {me && (
        <>
          <div className="balance-hero">
            <div className="balance-label">موجودی شما</div>
            <div className="balance-value">
              <span style={{ fontSize: 24, marginInlineEnd: 4 }}>$</span>
              {formatUsd(me.balance_usd)}
            </div>
            <div className="balance-sub">
              {me.username ? `@${me.username}` : `ID: ${me.telegram_id}`}
            </div>
          </div>

          <div className="card">
            <div className="row" style={{ gap: 12 }}>
              <div className="avatar" aria-hidden>
                {initial(tgUser?.first_name, me.username ?? undefined)}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="title">
                  {tgUser?.first_name ?? me.username ?? "کاربر"}
                </div>
                <div className="muted" style={{ direction: "ltr" }}>
                  {me.username ? `@${me.username}` : `ID: ${me.telegram_id}`}
                </div>
              </div>
              <span className="badge">{me.role}</span>
            </div>
          </div>

          <div className="card">
            <div className="title">واریز و برداشت</div>
            <p className="muted mt-2">
              برای شارژ کیف پول به ربات بازگردید و گزینه «💳 واریز» را انتخاب
              کنید (ارز دیجیتال از طریق NowPayments / Plisio). برای برداشت USDT روی شبکهٔ
              BSC از دکمهٔ زیر استفاده کنید.
            </p>
            <div className="row mt-3" style={{ gap: 8 }}>
              <button className="btn btn-ghost" onClick={close} style={{ flex: 1 }}>
                بازگشت به ربات
              </button>
              <Link
                to="/withdraw"
                onClick={() => haptic.selection()}
                className="btn btn-primary"
                style={{
                  flex: 1,
                  textDecoration: "none",
                  textAlign: "center",
                }}
              >
                برداشت USDT
              </Link>
            </div>
          </div>

          {me.is_admin && (
            <Link
              to="/admin"
              onClick={() => haptic.selection()}
              style={{ textDecoration: "none", color: "inherit" }}
            >
              <div className="card">
                <div className="row" style={{ alignItems: "center" }}>
                  <div style={{ fontSize: 24 }}>🛠</div>
                  <div style={{ flex: 1 }}>
                    <div className="title">پنل مدیریت</div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      ورود به پنل ادمین
                    </div>
                  </div>
                  <div className="muted">›</div>
                </div>
              </div>
            </Link>
          )}

          <div className="card" style={{ marginTop: 12, padding: 12 }}>
            <div className="title">تاریخچه تراکنش‌ها</div>
          </div>
          <TransactionsList />
        </>
      )}
    </div>
  );
}
