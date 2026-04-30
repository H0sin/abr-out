import { FormEvent, useEffect, useState } from "react";
import { ApiError, Listing, api } from "../api";

export function Sell() {
  const [mine, setMine] = useState<Listing[] | null>(null);
  const [title, setTitle] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [price, setPrice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setMine(await api.listMyListings());
    } catch (e) {
      setError(e instanceof Error ? e.message : "خطا");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      await api.createListing({
        title: title.trim(),
        iran_host: host.trim(),
        port: parseInt(port, 10),
        price_per_gb_usd: parseFloat(price),
      });
      setSuccess("درخواست شما ثبت شد. پس از تأیید ادمین فعال می‌شود.");
      setTitle("");
      setHost("");
      setPort("");
      setPrice("");
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("success");
      load();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "خطا در ثبت";
      setError(msg);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h2>فروش outbound</h2>
      <p className="muted">
        outbound ایران خود را اضافه کنید. پس از تأیید ادمین در مارکت قابل خرید است.
      </p>

      {error && <div className="error">{error}</div>}
      {success && <div className="success">{success}</div>}

      <form onSubmit={submit} className="card">
        <div className="field">
          <label>عنوان</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={100}
            placeholder="مثلاً: تهران - مخابرات"
          />
        </div>
        <div className="field">
          <label>هاست/IP ایران</label>
          <input
            type="text"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            required
            placeholder="1.2.3.4 یا host.ir"
          />
        </div>
        <div className="field">
          <label>پورت</label>
          <input
            type="number"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            required
            min={1}
            max={65535}
          />
        </div>
        <div className="field">
          <label>قیمت هر GB (USD)</label>
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            required
            min={0.01}
            step={0.01}
            placeholder="مثلاً 0.5"
          />
        </div>
        <button className="btn" disabled={busy}>
          {busy ? "در حال ارسال..." : "ثبت"}
        </button>
      </form>

      <h3 style={{ marginTop: 24 }}>outboundهای من</h3>
      {mine === null && <p className="muted">در حال بارگذاری...</p>}
      {mine && mine.length === 0 && (
        <p className="muted center">هنوز outbound‌ای ثبت نکرده‌اید.</p>
      )}
      {mine?.map((l) => (
        <div key={l.id} className="card">
          <div className="row">
            <div>
              <strong>{l.title}</strong>
              <div className="muted">
                {l.iran_host}:{l.port} • {l.price_per_gb_usd}$/GB
              </div>
            </div>
            <span className={`badge badge-${l.status}`}>{l.status}</span>
          </div>
          <div className="muted" style={{ marginTop: 6 }}>
            فروش: {l.sales_count}
          </div>
        </div>
      ))}
    </div>
  );
}
