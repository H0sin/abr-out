import { useEffect, useState } from "react";
import { Me, api } from "../api";

export function Wallet() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch((e) => setError(e.message));
  }, []);

  function close() {
    window.Telegram?.WebApp?.close();
  }

  return (
    <div>
      <h2>کیف پول</h2>
      {error && <div className="error">{error}</div>}
      {me === null && !error && <p className="muted">در حال بارگذاری...</p>}
      {me && (
        <>
          <div className="card center">
            <div className="muted">موجودی</div>
            <div className="balance">{me.balance_usd}$</div>
            <div className="muted">
              {me.username ? `@${me.username}` : `ID: ${me.telegram_id}`}
            </div>
          </div>

          <div className="card">
            <strong>افزایش موجودی</strong>
            <p className="muted" style={{ marginTop: 8 }}>
              برای افزایش موجودی، به ربات بازگردید و از منوی اصلی گزینه «💰 افزایش
              موجودی» را انتخاب کنید. پرداخت از طریق SwapWallet (USDT/IRT) انجام
              می‌شود.
            </p>
            <button className="btn" onClick={close} style={{ marginTop: 8 }}>
              بازگشت به ربات
            </button>
          </div>
        </>
      )}
    </div>
  );
}
