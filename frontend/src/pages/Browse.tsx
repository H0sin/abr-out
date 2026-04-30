import { useEffect, useState } from "react";
import { ApiError, Listing, api } from "../api";

export function Browse() {
  const [listings, setListings] = useState<Listing[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    api
      .listListings()
      .then(setListings)
      .catch((e) => setError(e.message));
  }, []);

  async function buy(l: Listing) {
    setError(null);
    setSuccess(null);
    setBusyId(l.id);
    try {
      const cfg = await api.buyConfig(l.id);
      setSuccess(`کانفیگ ساخته شد: ${cfg.panel_client_email}`);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("success");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "خطا در خرید";
      setError(msg);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <h2>مارکت‌پلیس</h2>
      <p className="muted">لیست outboundهای فعال — کم‌قیمت‌ترین اول</p>

      {error && <div className="error">{error}</div>}
      {success && <div className="success">{success}</div>}

      {listings === null && <p className="muted">در حال بارگذاری...</p>}
      {listings && listings.length === 0 && (
        <p className="muted center">هیچ outbound فعالی موجود نیست.</p>
      )}

      {listings?.map((l) => (
        <div key={l.id} className="card">
          <div className="row">
            <div>
              <strong>{l.title}</strong>
              <div className="muted">
                {l.iran_host}:{l.port}
                {l.avg_ping_ms !== null && ` • ${l.avg_ping_ms}ms`}
              </div>
            </div>
            <div className="center">
              <div>
                <strong>{l.price_per_gb_usd}$</strong>
              </div>
              <div className="muted">هر GB</div>
            </div>
          </div>
          <button
            className="btn"
            disabled={busyId === l.id}
            onClick={() => buy(l)}
            style={{ marginTop: 12 }}
          >
            {busyId === l.id ? "در حال ساخت..." : "خرید"}
          </button>
        </div>
      ))}
    </div>
  );
}
