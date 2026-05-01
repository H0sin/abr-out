import { useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { adminApi, WalletSummary, WalletTx } from "../../api";
import { CopyIcon, RefreshIcon } from "../../components/icons";
import { EmptyState, Skeleton } from "../../components/ui";
import { useResource } from "../../lib/useApi";
import { useToast } from "../../lib/toast";
import { haptic } from "../../lib/useTelegram";

type AssetFilter = "all" | "usdt" | "bnb";

function fmtAmount(raw: string | number, max = 6): string {
  const n = typeof raw === "number" ? raw : parseFloat(raw);
  if (!Number.isFinite(n)) return String(raw);
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: max,
  });
}

function shortAddr(a: string): string {
  if (!a || a.length < 12) return a || "";
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

function timeAgoFa(ts: number | null): string {
  if (!ts) return "—";
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return `${diff} ثانیه قبل`;
  if (diff < 3600) return `${Math.floor(diff / 60)} دقیقه قبل`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ساعت قبل`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} روز قبل`;
  try {
    return new Date(ts * 1000).toLocaleDateString("fa-IR");
  } catch {
    return new Date(ts * 1000).toISOString().slice(0, 10);
  }
}

export function AdminWithdrawalWallet() {
  const toast = useToast();
  const [asset, setAsset] = useState<AssetFilter>("all");
  const [page, setPage] = useState(1);
  const size = 25;
  const [showQR, setShowQR] = useState(false);

  const summary = useResource(() => adminApi.getWalletSummary(), []);
  const txs = useResource(
    () => adminApi.listWalletTxs({ asset, page, size }),
    [asset, page],
  );

  async function copyAddr() {
    if (!summary.data?.address) return;
    try {
      await navigator.clipboard.writeText(summary.data.address);
      haptic.success();
      toast.success("آدرس کپی شد");
    } catch {
      toast.error("کپی نشد");
    }
  }

  async function copyHash(h: string) {
    try {
      await navigator.clipboard.writeText(h);
      haptic.selection();
      toast.success("هش کپی شد");
    } catch {
      toast.error("کپی نشد");
    }
  }

  function refreshAll() {
    haptic.light();
    summary.refetch();
    txs.refetch();
  }

  const sourceLabel = useMemo(() => {
    const s = txs.data?.source;
    if (s === "bscscan") return "BscScan";
    if (s === "rpc") return "RPC شبکه";
    return "—";
  }, [txs.data?.source]);

  return (
    <div>
      <header
        className="row"
        style={{ marginBottom: 12, alignItems: "center" }}
      >
        <h2 style={{ margin: 0, flex: 1 }}>کیف پول برداشت</h2>
        <button
          className="chip"
          onClick={refreshAll}
          aria-label="بارگذاری مجدد"
        >
          <RefreshIcon />
        </button>
      </header>

      <div className="alert alert-info" style={{ marginBottom: 8 }}>
        این کیف پول مستقیماً از شبکهٔ بایننس (BSC) خوانده می‌شود و کاملاً
        مستقل از کیف پول داخلی کاربران ربات است. فقط ادمین می‌تواند آن را
        ببیند.
      </div>

      {summary.error && (
        <div className="alert alert-error">{summary.error}</div>
      )}

      {summary.loading && !summary.data && (
        <div className="card">
          <Skeleton width="40%" />
          <div style={{ height: 8 }} />
          <Skeleton width="90%" />
          <div style={{ height: 8 }} />
          <Skeleton width="70%" />
        </div>
      )}

      {summary.data && !summary.data.configured && (
        <div className="alert alert-error">
          کلید خصوصی هات‌ولت تنظیم نشده است
          (<span dir="ltr">BSC_HOT_WALLET_PRIVATE_KEY</span>). تا زمانی که کلید
          ست نشود، آدرس و موجودی نمایش داده نمی‌شوند.
        </div>
      )}

      {summary.data && summary.data.configured && (
        <>
          {/* Address card */}
          <article className="card">
            <div className="row" style={{ alignItems: "center", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="title">آدرس واریز</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  شبکه: <b>{summary.data.network}</b>
                </div>
              </div>
              <span className="badge badge-active">USDT BEP20</span>
            </div>
            <div
              className="card"
              onClick={copyAddr}
              style={{
                direction: "ltr",
                wordBreak: "break-all",
                fontSize: 13,
                cursor: "pointer",
                padding: 10,
                marginTop: 8,
              }}
              title="برای کپی کلیک کنید"
            >
              {summary.data.address}
            </div>
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <button className="btn" onClick={copyAddr}>
                <CopyIcon /> کپی آدرس
              </button>
              <button
                className="btn"
                onClick={() => setShowQR((v) => !v)}
              >
                {showQR ? "بستن QR" : "نمایش QR"}
              </button>
            </div>
            {showQR && (
              <div className="qr-box" style={{ marginTop: 12 }}>
                <QRCodeSVG
                  value={summary.data.address ?? ""}
                  size={180}
                  level="M"
                />
              </div>
            )}
            <div
              className="alert alert-info"
              style={{ marginTop: 10, fontSize: 12 }}
            >
              ⚠️ فقط <b>USDT روی شبکهٔ BEP20 (BSC)</b> یا <b>BNB</b> به این
              آدرس واریز کنید. ارسال در شبکه‌های دیگر (TRC20, ERC20, ...) باعث
              از دست رفتن دارایی می‌شود.
            </div>
          </article>

          {/* Balances card */}
          <article className="card">
            <div className="title" style={{ marginBottom: 8 }}>
              موجودی شبکه
            </div>
            <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 140 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  USDT
                </div>
                <div className="num" style={{ fontSize: 22 }}>
                  {fmtAmount(summary.data.usdt_balance, 4)}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  ≈ ${fmtAmount(summary.data.usdt_balance, 2)}
                </div>
              </div>
              <div style={{ flex: 1, minWidth: 140 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  BNB (برای gas)
                </div>
                <div className="num" style={{ fontSize: 22 }}>
                  {fmtAmount(summary.data.bnb_balance, 6)}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  ≈ ${fmtAmount(summary.data.bnb_balance_usd, 2)}
                  {parseFloat(summary.data.bnb_price_usd) > 0 && (
                    <>
                      {" "}
                      (BNB = ${fmtAmount(summary.data.bnb_price_usd, 2)})
                    </>
                  )}
                </div>
              </div>
            </div>
          </article>
        </>
      )}

      {/* Transactions */}
      <div className="row" style={{ alignItems: "center", marginTop: 16 }}>
        <h3 style={{ margin: 0, flex: 1 }}>تراکنش‌های شبکه</h3>
        <span className="muted" style={{ fontSize: 11 }}>
          منبع: {sourceLabel}
        </span>
      </div>

      <div className="chips" style={{ marginTop: 8 }}>
        {(["all", "usdt", "bnb"] as AssetFilter[]).map((a) => (
          <button
            key={a}
            type="button"
            className={`chip${asset === a ? " active" : ""}`}
            onClick={() => {
              haptic.selection();
              setAsset(a);
              setPage(1);
            }}
          >
            {a === "all" ? "همه" : a.toUpperCase()}
          </button>
        ))}
      </div>

      {txs.error && <div className="alert alert-error">{txs.error}</div>}
      {txs.data?.note && (
        <div className="alert alert-info" style={{ marginTop: 8 }}>
          {txs.data.note}
        </div>
      )}

      {txs.loading && !txs.data && (
        <div className="card">
          <Skeleton width="80%" />
          <div style={{ height: 6 }} />
          <Skeleton width="60%" />
        </div>
      )}

      {txs.data && txs.data.items.length === 0 && !txs.loading && (
        <EmptyState emoji="🪙" title="تراکنشی یافت نشد" />
      )}

      {txs.data?.items.map((t) => (
        <TxRow key={`${t.hash}-${t.asset}`} tx={t} onCopyHash={copyHash} />
      ))}

      {/* Pagination */}
      {txs.data && txs.data.items.length > 0 && (
        <div className="row" style={{ gap: 8, marginTop: 12 }}>
          <button
            className="btn"
            disabled={page <= 1 || txs.loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            قبلی
          </button>
          <div className="muted" style={{ alignSelf: "center", flex: 1, textAlign: "center" }}>
            صفحه <span className="num">{page}</span>
          </div>
          <button
            className="btn"
            disabled={txs.loading || (txs.data?.items.length ?? 0) < size}
            onClick={() => setPage((p) => p + 1)}
          >
            بعدی
          </button>
        </div>
      )}
    </div>
  );
}

function TxRow({
  tx,
  onCopyHash,
}: {
  tx: WalletTx;
  onCopyHash: (h: string) => void;
}) {
  const isIn = tx.direction === "in";
  const isSelf = tx.direction === "self";
  const sign = isSelf ? "" : isIn ? "+" : "−";
  const color = isSelf
    ? "var(--muted, #888)"
    : isIn
      ? "#15803d"
      : "#b91c1c";
  const counterparty = isIn ? tx.from : tx.to;

  return (
    <article className="card" style={{ padding: 12 }}>
      <div className="row" style={{ alignItems: "center", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 6, alignItems: "center" }}>
            <span
              className="num"
              style={{ color, fontSize: 16, fontWeight: 600 }}
            >
              {sign}
              {fmtAmount(tx.amount, 6)}
            </span>
            <span className="muted" style={{ fontSize: 12 }}>
              {tx.asset}
            </span>
            {tx.status === "failed" && (
              <span className="badge badge-rejected">ناموفق</span>
            )}
            {isSelf && (
              <span className="badge badge-pending">به خودش</span>
            )}
          </div>
          <div
            className="muted"
            style={{ fontSize: 11, marginTop: 2, direction: "ltr" }}
          >
            {isIn ? "از" : "به"} {shortAddr(counterparty)}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            {timeAgoFa(tx.timestamp)}
          </div>
        </div>
        <div className="row" style={{ gap: 4 }}>
          <button
            className="chip"
            onClick={() => onCopyHash(tx.hash)}
            title="کپی هش تراکنش"
          >
            <CopyIcon />
          </button>
          <a
            className="chip"
            href={tx.explorer_url}
            target="_blank"
            rel="noreferrer"
            title="مشاهده در BscScan"
          >
            ↗
          </a>
        </div>
      </div>
    </article>
  );
}
