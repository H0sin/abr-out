import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { ApiError, Config, Listing, api, topupDeepLink } from "../api";
import {
  EmptyState,
  Modal,
  PingCircle,
  SkeletonCard,
  StabilityPct,
} from "../components/ui";
import { CopyIcon, RefreshIcon } from "../components/icons";
import { useResource } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useMe } from "../lib/MeContext";
import { haptic, openTelegramLink } from "../lib/useTelegram";

type SortKey = "price" | "ping" | "sales" | "stability";

function fmtUsd(raw: string | number): string {
  const n = typeof raw === "number" ? raw : parseFloat(raw);
  if (!Number.isFinite(n)) return String(raw);
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  });
}

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
  const location = useLocation();

  const sorted = useMemo(() => {
    if (!listings) return null;
    const xs = [...listings];
    xs.sort((a, b) => {
      if (sort === "price")
        return parseFloat(a.price_per_gb_usd) - parseFloat(b.price_per_gb_usd);
      if (sort === "ping")
        return (a.avg_ping_ms ?? 9999) - (b.avg_ping_ms ?? 9999);
      if (sort === "stability")
        return (b.stability_pct ?? -1) - (a.stability_pct ?? -1);
      return b.sales_count - a.sales_count;
    });
    return xs;
  }, [listings, sort]);

  useEffect(() => {
    if (!sorted || openListing) return;
    const raw = new URLSearchParams(location.search).get("listing");
    if (!raw) return;
    const listingId = Number.parseInt(raw, 10);
    if (!Number.isFinite(listingId)) return;
    const match = sorted.find((item) => item.id === listingId);
    if (match) {
      setOpenListing(match);
    }
  }, [location.search, openListing, sorted]);

  function closeBuyModal() {
    setOpenListing(null);
    if (new URLSearchParams(location.search).has("listing")) {
      nav("/browse", { replace: true });
    }
  }

  return (
    <div>
      <header className="row" style={{ marginBottom: 12, alignItems: "center" }}>
        <p className="muted" style={{ margin: 0, flex: 1 }}>
          اوت‌باندهای فعال — مرتب‌سازی خود را انتخاب کنید
        </p>
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

      <div className="chips">
        <SortChip active={sort === "price"} onClick={() => setSort("price")}>
          ارزان‌ترین
        </SortChip>
        <SortChip active={sort === "ping"} onClick={() => setSort("ping")}>
          سریع‌ترین
        </SortChip>
        <SortChip
          active={sort === "stability"}
          onClick={() => setSort("stability")}
        >
          پایدارترین
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
          <div className="row" style={{ alignItems: "center", gap: 12 }}>
            <PingCircle ms={l.avg_ping_ms} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                className="title"
                style={{
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                اوت‌باند <span className="num">#{l.id}</span>
              </div>
              <div
                className="row gap-2 mt-2"
                style={{ justifyContent: "flex-start", flexWrap: "wrap" }}
              >
                <StabilityPct pct={l.stability_pct} />
                <span className="stat-pill" title="مجموع ترافیک فروخته‌شده">
                  مجموع فروش <span className="num">{l.total_gb_sold.toFixed(1)}</span> GB
                </span>
              </div>
            </div>
          </div>
          <button
            className="btn btn-primary btn-buy mt-3"
            onClick={() => {
              haptic.selection();
              setOpenListing(l);
            }}
          >
            <span>خرید کانفیگ</span>
            <span className="btn-buy-price">
              <span className="btn-buy-unit">گیگی</span>
              <span className="num">{fmtUsd(l.buyer_price_per_gb_usd)}</span>
              <span className="btn-buy-unit">دلار</span>
            </span>
          </button>
        </article>
      ))}

      <BuyModal
        listing={openListing}
        onClose={closeBuyModal}
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
  const [customExpiry, setCustomExpiry] = useState(false);
  const [customDays, setCustomDays] = useState("");
  const [gb, setGb] = useState<string>("");
  const [autoDisable, setAutoDisable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Config | null>(null);
  const [copied, setCopied] = useState(false);

  // Reset on each open
  const open = listing !== null;
  useResetOnOpen(open, () => {
    setStep("form");
    setName("");
    setDays(null);
    setCustomExpiry(false);
    setCustomDays("");
    setGb("");
    setAutoDisable(true);
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
    if (!/^[A-Za-z0-9 ._-]+$/.test(trimmed)) {
      toastError("نام فقط با حروف انگلیسی، عدد، فاصله، نقطه و خط تیره");
      return;
    }
    let expiry_days: number | null = days;
    if (customExpiry) {
      const d = parseInt(customDays, 10);
      if (!Number.isFinite(d) || d <= 0 || d > 3650) {
        toastError("تعداد روز نامعتبر است (۱ تا ۳۶۵۰)");
        return;
      }
      expiry_days = d;
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
        expiry_days,
        total_gb_limit,
        auto_disable_on_price_increase: autoDisable,
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
          : `خرید اوت‌باند #${listing.id}`
      }
    >
      {step === "form" && insufficient && (
        <div>
          <div className="alert alert-error" style={{ marginTop: 4 }}>
            موجودی کافی نیست. حداقل <b className="num">{MIN_BALANCE}</b>$ باید
            شارژ کنی.
          </div>
          <div className="muted" style={{ marginTop: 6 }}>
            موجودی فعلی: <span className="num">{fmtUsd(balance)}</span>$
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
          <div className="muted" style={{ marginBottom: 8 }}>
            قیمت: <span className="num">{fmtUsd(listing.buyer_price_per_gb_usd)}</span>$ / گیگ
            — موجودی: <span className="num">{fmtUsd(balance)}</span>$
          </div>

          <div className="field">
            <label className="field-label">نام کانفیگ</label>
            <input
              className="input"
              type="text"
              maxLength={32}
              dir="ltr"
              placeholder="e.g. mobile or laptop"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
              فقط حروف انگلیسی، عدد، فاصله، نقطه و خط تیره
            </div>
          </div>

          <div className="alert alert-info" style={{ marginTop: 8 }}>
            مقادیر <b>مدت اعتبار</b> و <b>محدودیت حجم</b> فقط برای تنظیم محدودیت
            توسط شماست (مثلاً اگر می‌خواهید این کانفیگ را به دیگری بفروشید).
            هیچ تأثیری روی قیمت یا عملکرد ربات ندارند.
          </div>

          <div className="field">
            <label className="field-label">مدت اعتبار</label>
            <div className="chips">
              {EXPIRY_PRESETS.map((p) => (
                <button
                  key={String(p.days)}
                  type="button"
                  className={`chip${!customExpiry && days === p.days ? " active" : ""}`}
                  onClick={() => {
                    haptic.selection();
                    setCustomExpiry(false);
                    setDays(p.days);
                  }}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                className={`chip${customExpiry ? " active" : ""}`}
                onClick={() => {
                  haptic.selection();
                  setCustomExpiry(true);
                }}
              >
                دلخواه
              </button>
            </div>
            {customExpiry && (
              <input
                className="input mt-2"
                type="text"
                inputMode="numeric"
                dir="ltr"
                placeholder="تعداد روز (۱ تا ۳۶۵۰)"
                value={customDays}
                onChange={(e) =>
                  setCustomDays(e.target.value.replace(/\D/g, ""))
                }
              />
            )}
          </div>

          <div className="field">
            <label className="field-label">محدودیت حجم (گیگابایت — خالی = نامحدود)</label>
            <input
              className="input"
              type="number"
              min={0}
              step="0.1"
              placeholder="نامحدود"
              value={gb}
              onChange={(e) => setGb(e.target.value)}
            />
          </div>

          <label className={`toggle-card${autoDisable ? " is-on" : ""}`}>
            <div className="toggle-card-text">
              <div className="toggle-card-title">
                غیرفعال‌سازی خودکار در صورت گرانی
              </div>
              <div className="toggle-card-hint">
                اگر فروشنده قیمت هر گیگ را بالا برد، این کانفیگ به‌صورت خودکار
                خاموش می‌شود تا از کسر ناخواسته از موجودی شما جلوگیری شود.
              </div>
            </div>
            <span className="switch">
              <input
                type="checkbox"
                checked={autoDisable}
                onChange={(e) => setAutoDisable(e.target.checked)}
              />
              <span className="switch-slider" />
            </span>
          </label>

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
