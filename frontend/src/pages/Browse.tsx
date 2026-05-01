import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { ApiError, Config, Listing, api, topupDeepLink } from "../api";
import { EmptyState, Modal, PingPill, SkeletonCard } from "../components/ui";
import { CopyIcon, RefreshIcon } from "../components/icons";
import { useResource } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useMe } from "../lib/MeContext";
import { haptic, openTelegramLink } from "../lib/useTelegram";

type SortKey = "price" | "ping" | "sales";

const MIN_BALANCE = 0.5;
const EXPIRY_PRESETS: { label: string; days: number | null }[] = [
  { label: "نامحدود", days: null },
  { label: "۷ روز", days: 7 },
  { label: "۳۰ روز", days: 30 },
  { label: "۶۰ روز", days: 60 },
  { label: "۹۰ روز", days: 90 },
];

export function Browse() {
  const { data: listings, loading, error, refetch } = useResource(
    () => api.listListings(),
  );
  const [sort, setSort] = useState<SortKey>("price");
  const [openListing, setOpenListing] = useState<Listing | null>(null);
  const toast = useToast();
  const { refresh: refreshMe } = useMe();
  const nav = useNavigate();

  const sorted = useMemo(() => {
    if (!listings) return null;
    const xs = [...listings];
    xs.sort((a, b) => {
      if (sort === "price")
        return parseFloat(a.price_per_gb_usd) - parseFloat(b.price_per_gb_usd);
      if (sort === "ping")
        return (a.avg_ping_ms ?? 9999) - (b.avg_ping_ms ?? 9999);
      return b.sales_count - a.sales_count;
    });
    return xs;
  }, [listings, sort]);

  return (
    <div>
      <header className="row" style={{ marginBottom: 12 }}>
        <h2>مارکت‌پلیس</h2>
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
      <p className="muted" style={{ marginTop: 0 }}>
        اوت‌باندهای فعال — مرتب‌سازی خود را انتخاب کنید
      </p>

      <div className="chips">
        <SortChip active={sort === "price"} onClick={() => setSort("price")}>
          ارزان‌ترین
        </SortChip>
        <SortChip active={sort === "ping"} onClick={() => setSort("ping")}>
          سریع‌ترین
        </SortChip>
        <SortChip active={sort === "sales"} onClick={() => setSort("sales")}>
          پرفروش‌ترین
        </SortChip>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading && !sorted && (
        <>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </>
      )}

      {sorted && sorted.length === 0 && (
        <EmptyState
          emoji="🛰"
          title="هیچ اوت‌باند فعالی موجود نیست"
          hint="کمی بعد دوباره سر بزنید."
        />
      )}

      {sorted?.map((l) => (
        <article key={l.id} className="card">
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                className="title"
                style={{
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {l.title}
              </div>
              <div
                className="muted"
                style={{ direction: "ltr", textAlign: "right", marginTop: 2 }}
              >
                {l.iran_host}
                <span style={{ opacity: 0.6 }}>:</span>
                {l.port}
              </div>
              <div className="row gap-2 mt-2" style={{ justifyContent: "flex-start" }}>
                <PingPill ms={l.avg_ping_ms} />
                <span className="badge">
                  <span className="num">{l.sales_count}</span> فروش
                </span>
                {l.seller_username && (
                  <span className="muted" style={{ direction: "ltr" }}>
                    @{l.seller_username}
                  </span>
                )}
              </div>
            </div>
            <div className="price-tag">
              <span className="num-big num">{l.price_per_gb_usd}</span>
              <span className="num-small">$/GB</span>
            </div>
          </div>
          <button
            className="btn btn-primary mt-3"
            onClick={() => {
              haptic.selection();
              setOpenListing(l);
            }}
          >
            خرید کانفیگ
          </button>
        </article>
      ))}

      <BuyModal
        listing={openListing}
        onClose={() => setOpenListing(null)}
        onCreated={() => {
          refreshMe();
        }}
        onGoMyConfigs={() => {
          setOpenListing(null);
          nav("/my");
        }}
        toastSuccess={(m) => toast.success(m)}
        toastError={(m) => toast.error(m)}
      />
    </div>
  );
}

type Step = "form" | "result";

function BuyModal({
  listing,
  onClose,
  onCreated,
  onGoMyConfigs,
  toastSuccess,
  toastError,
}: {
  listing: Listing | null;
  onClose: () => void;
  onCreated: () => void;
  onGoMyConfigs: () => void;
  toastSuccess: (m: string) => void;
  toastError: (m: string) => void;
}) {
  const { me } = useMe();
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [gb, setGb] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Config | null>(null);
  const [copied, setCopied] = useState(false);

  // Reset on each open
  const open = listing !== null;
  useResetOnOpen(open, () => {
    setStep("form");
    setName("");
    setDays(null);
    setGb("");
    setBusy(false);
    setCreated(null);
    setCopied(false);
  });

  if (!listing) return null;

  const balance = me ? parseFloat(me.balance_usd) : 0;
  const insufficient = balance < MIN_BALANCE;
  const deepLink = topupDeepLink(me?.bot_username ?? null);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) {
      toastError("نام کانفیگ را وارد کنید");
      return;
    }
    let total_gb_limit: number | null = null;
    if (gb.trim()) {
      const n = parseFloat(gb);
      if (!Number.isFinite(n) || n <= 0) {
        toastError("مقدار حجم نامعتبر است");
        return;
      }
      total_gb_limit = n;
    }
    setBusy(true);
    try {
      const cfg = await api.buyConfig({
        listing_id: listing!.id,
        name: trimmed,
        expiry_days: days,
        total_gb_limit,
      });
      haptic.success();
      toastSuccess("کانفیگ ساخته شد");
      onCreated();
      setCreated(cfg);
      setStep("result");
    } catch (e) {
      haptic.error();
      toastError(e instanceof ApiError ? e.message : "خطا در خرید");
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.vless_link);
      setCopied(true);
      haptic.success();
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toastError("کپی نشد");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        step === "result"
          ? "کانفیگ ساخته شد"
          : `خرید از ${listing.title}`
      }
    >
      {step === "form" && insufficient && (
        <div>
          <div className="alert alert-error" style={{ marginTop: 4 }}>
            موجودی کافی نیست. حداقل <b className="num">{MIN_BALANCE}</b>$ باید
            شارژ کنی.
          </div>
          <div className="muted" style={{ marginTop: 6 }}>
            موجودی فعلی: <span className="num">{balance.toFixed(2)}</span>$
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={onClose}>
              بستن
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                if (deepLink) openTelegramLink(deepLink);
                else window.Telegram?.WebApp?.close();
              }}
            >
              افزایش موجودی
            </button>
          </div>
        </div>
      )}

      {step === "form" && !insufficient && (
        <div>
          <div className="muted" style={{ marginBottom: 4 }}>
            قیمت: <span className="num">{listing.price_per_gb_usd}</span>$ / گیگ
            — موجودی: <span className="num">{balance.toFixed(2)}</span>$
          </div>

          <div className="field">
            <label>نام کانفیگ</label>
            <input
              type="text"
              maxLength={32}
              placeholder="مثلاً موبایل یا لپ‌تاپ"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="field">
            <label>مدت اعتبار</label>
            <div className="chips">
              {EXPIRY_PRESETS.map((p) => (
                <button
                  key={String(p.days)}
                  type="button"
                  className={`chip${days === p.days ? " active" : ""}`}
                  onClick={() => {
                    haptic.selection();
                    setDays(p.days);
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <label>محدودیت حجم (گیگابایت — خالی = نامحدود)</label>
            <input
              type="number"
              min={0}
              step="0.1"
              placeholder="نامحدود"
              value={gb}
              onChange={(e) => setGb(e.target.value)}
            />
          </div>

          <div className="modal-actions">
            <button className="btn" onClick={onClose} disabled={busy}>
              انصراف
            </button>
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={busy}
            >
              {busy ? (
                <>
                  <span className="spinner" /> در حال ساخت...
                </>
              ) : (
                "تأیید و ساخت"
              )}
            </button>
          </div>
        </div>
      )}

      {step === "result" && created && (
        <div>
          <div className="muted">
            نام: <b>{created.name}</b>
          </div>
          <div className="qr-box">
            <QRCodeSVG value={created.vless_link} size={196} level="M" />
          </div>
          <div
            className="card"
            onClick={copyLink}
            style={{
              direction: "ltr",
              wordBreak: "break-all",
              fontSize: 12,
              cursor: "pointer",
              padding: 10,
            }}
          >
            {created.vless_link}
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={copyLink}>
              <CopyIcon />
              {copied ? " کپی شد ✓" : " کپی لینک"}
            </button>
            <button className="btn btn-primary" onClick={onGoMyConfigs}>
              کانفیگ‌های من
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

// Helper: run reset callback whenever `open` flips to true.
function useResetOnOpen(open: boolean, reset: () => void) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useMemo(() => {
    if (open) reset();
  }, [open]);
}

function SortChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={`chip${active ? " active" : ""}`}
      onClick={() => {
        haptic.selection();
        onClick();
      }}
    >
      {children}
    </button>
  );
}
