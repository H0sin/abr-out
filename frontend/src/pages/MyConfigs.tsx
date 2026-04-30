import { useEffect, useState } from "react";
import { Config, api } from "../api";

export function MyConfigs() {
  const [configs, setConfigs] = useState<Config[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  useEffect(() => {
    api
      .listConfigs()
      .then(setConfigs)
      .catch((e) => setError(e.message));
  }, []);

  async function copy(id: number, link: string) {
    try {
      await navigator.clipboard.writeText(link);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      // ignore
    }
  }

  function formatBytes(n: number): string {
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  return (
    <div>
      <h2>کانفیگ‌های من</h2>
      {error && <div className="error">{error}</div>}
      {configs === null && <p className="muted">در حال بارگذاری...</p>}
      {configs && configs.length === 0 && (
        <p className="muted center">هنوز کانفیگی نخریده‌اید.</p>
      )}
      {configs?.map((c) => (
        <div key={c.id} className="card">
          <div className="row">
            <strong>{c.listing_title}</strong>
            <span className={`badge badge-${c.status}`}>{c.status}</span>
          </div>
          <div className="muted" style={{ marginTop: 4 }}>
            مصرف: {formatBytes(c.last_traffic_bytes)}
          </div>
          <div className="copy-link" onClick={() => copy(c.id, c.vless_link)}>
            {c.vless_link}
          </div>
          <button
            className="btn btn-secondary"
            onClick={() => copy(c.id, c.vless_link)}
            style={{ marginTop: 8 }}
          >
            {copiedId === c.id ? "کپی شد ✓" : "کپی لینک"}
          </button>
        </div>
      ))}
    </div>
  );
}
