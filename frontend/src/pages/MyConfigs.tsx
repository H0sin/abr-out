import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Config, api } from "../api";
import { EmptyState, Modal, SkeletonCard, StatusBadge } from "../components/ui";
import { CopyIcon, RefreshIcon } from "../components/icons";
import { useResource } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { haptic } from "../lib/useTelegram";

// ~10GB monthly bucket — listings don't expose a quota yet, so we render
// usage on a soft scale that adapts to actual consumption.
function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function trafficPercent(bytes: number, limitGb: number | null): number {
  if (limitGb && limitGb > 0) {
    const gb = bytes / (1024 * 1024 * 1024);
    return Math.min(100, Math.max(2, (gb / limitGb) * 100));
  }
  if (!Number.isFinite(bytes) || bytes <= 0) return 4; // tiny visual hint
  // Adaptive bucket: scale against next power-of-2 GB above current usage.
  const gb = bytes / (1024 * 1024 * 1024);
  const bucket = Math.max(1, Math.pow(2, Math.ceil(Math.log2(gb + 0.01))));
  return Math.min(100, Math.max(4, (gb / bucket) * 100));
}

function expiryLabel(iso: string | null): string {
  if (!iso) return "نامحدود";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "—";
  const ms = t - Date.now();
  if (ms <= 0) return "منقضی";
  const days = Math.ceil(ms / (24 * 3600 * 1000));
  return `${days} روز مانده`;
}

export function MyConfigs() {
  const { data: configs, loading, error, refetch } = useResource(
    () => api.listConfigs(),
  );
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [qrConfig, setQrConfig] = useState<Config | null>(null);
  const toast = useToast();

  async function copy(id: number, link: string) {
    try {
      await navigator.clipboard.writeText(link);
      haptic.light();
      setCopiedId(id);
      toast.success("لینک کپی شد");
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      toast.error("کپی ناموفق بود");
    }
  }

  return (
    <div>
      <header className="row" style={{ marginBottom: 12 }}>
        <h2>کانفیگ‌های من</h2>
        <button
          className="chip"
          onClick={() => {
            haptic.light();
            refetch();
          }}
          aria-label="بارگذاری مجدد"
        >
          <RefreshIcon />
        </button>
      </header>

      {error && <div className="alert alert-error">{error}</div>}

      {loading && !configs && (
        <>
          <SkeletonCard />
          <SkeletonCard />
        </>
      )}

      {configs && configs.length === 0 && (
        <EmptyState
          emoji="📡"
          title="هنوز کانفیگی نخریده‌اید"
          hint="از تب «خرید» اولین اوت‌باند خود را تهیه کنید."
        />
      )}

      {configs?.map((c) => (
        <article key={c.id} className="card">
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="title">{c.name}</div>
              <div className="muted" style={{ marginTop: 2 }}>
                {c.listing_title}
              </div>
              <div
                className="muted"
                style={{ direction: "ltr", textAlign: "right", marginTop: 2, fontSize: 12 }}
              >
                {c.panel_client_email}
              </div>
            </div>
            <StatusBadge status={c.status} />
          </div>

          <div className="row gap-2 mt-2" style={{ justifyContent: "flex-start" }}>
            <span className="badge">{expiryLabel(c.expiry_at)}</span>
            {c.total_gb_limit ? (
              <span className="badge">
                سقف <span className="num">{c.total_gb_limit}</span> GB
              </span>
            ) : (
              <span className="badge">حجم نامحدود</span>
            )}
          </div>

          <div className="row mt-3">
            <span className="muted">مصرف</span>
            <span className="num" style={{ fontWeight: 600 }}>
              {formatBytes(c.last_traffic_bytes)}
              {c.total_gb_limit ? ` / ${c.total_gb_limit} GB` : ""}
            </span>
          </div>
          <div className="traffic-bar">
            <div
              className="traffic-fill"
              style={{
                width: `${trafficPercent(c.last_traffic_bytes, c.total_gb_limit)}%`,
              }}
            />
          </div>

          <div
            className="copy-link"
            onClick={() => copy(c.id, c.vless_link)}
            title="برای کپی لمس کنید"
          >
            {c.vless_link}
          </div>

          <div className="row gap-2 mt-2">
            <button
              className="btn btn-secondary"
              style={{ flex: 1 }}
              onClick={() => copy(c.id, c.vless_link)}
            >
              {copiedId === c.id ? (
                "کپی شد ✓"
              ) : (
                <>
                  <CopyIcon /> کپی لینک
                </>
              )}
            </button>
            <button
              className="btn btn-secondary"
              style={{ flex: 1 }}
              onClick={() => {
                haptic.selection();
                setQrConfig(c);
              }}
            >
              QR
            </button>
          </div>
        </article>
      ))}

      <Modal
        open={qrConfig !== null}
        onClose={() => setQrConfig(null)}
        title={qrConfig ? qrConfig.name : ""}
      >
        {qrConfig && (
          <>
            <div className="qr-box">
              <QRCodeSVG value={qrConfig.vless_link} size={220} level="M" />
            </div>
            <div
              className="card"
              onClick={() => copy(qrConfig.id, qrConfig.vless_link)}
              style={{
                direction: "ltr",
                wordBreak: "break-all",
                fontSize: 12,
                cursor: "pointer",
                padding: 10,
              }}
            >
              {qrConfig.vless_link}
            </div>
            <div className="modal-actions">
              <button
                className="btn btn-primary"
                onClick={() => copy(qrConfig.id, qrConfig.vless_link)}
              >
                <CopyIcon /> کپی لینک
              </button>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
