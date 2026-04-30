import { Skeleton } from "../components/ui";
import { TransactionsList } from "../components/TransactionsList";
import { useMe } from "../lib/MeContext";
import { haptic } from "../lib/useTelegram";

function formatUsd(raw: string): string {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return raw;
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
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
      <h2>کیف پول</h2>

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
            <div className="title">افزایش موجودی</div>
            <p className="muted mt-2">
              برای شارژ کیف پول، به ربات بازگردید و گزینه «💰 افزایش موجودی» را
              انتخاب کنید. پرداخت از طریق SwapWallet (USDT/IRT) انجام می‌شود.
            </p>
            <button className="btn btn-primary mt-3" onClick={close}>
              بازگشت به ربات
            </button>
          </div>

          <div className="card" style={{ marginTop: 12, padding: 12 }}>
            <div className="title">تاریخچه تراکنش‌ها</div>
          </div>
          <TransactionsList />
        </>
      )}
    </div>
  );
}
