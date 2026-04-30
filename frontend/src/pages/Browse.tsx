import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, Listing, api } from "../api";
import { EmptyState, PingPill, SkeletonCard } from "../components/ui";
import { RefreshIcon } from "../components/icons";
import { useResource } from "../lib/useApi";
import { useToast } from "../lib/toast";
import { useMe } from "../lib/MeContext";
import { haptic, showConfirm } from "../lib/useTelegram";

type SortKey = "price" | "ping" | "sales";

export function Browse() {
  const { data: listings, loading, error, refetch } = useResource(
    () => api.listListings(),
  );
  const [busyId, setBusyId] = useState<number | null>(null);
  const [sort, setSort] = useState<SortKey>("price");
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

  async function buy(l: Listing) {
    const ok = await showConfirm(
      `خرید کانفیگ "${l.title}" با قیمت ${l.price_per_gb_usd}$ هر گیگابایت؟`,
    );
    if (!ok) return;
    setBusyId(l.id);
    try {
      const cfg = await api.buyConfig(l.id);
      haptic.success();
      toast.success(`کانفیگ ساخته شد: ${cfg.panel_client_email}`);
      refreshMe();
      setTimeout(() => nav("/my"), 600);
    } catch (e) {
      haptic.error();
      toast.error(e instanceof ApiError ? e.message : "خطا در خرید");
    } finally {
      setBusyId(null);
    }
  }

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
        outboundهای فعال — مرتب‌سازی خود را انتخاب کنید
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
          title="هیچ outbound فعالی موجود نیست"
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
            disabled={busyId === l.id}
            onClick={() => buy(l)}
          >
            {busyId === l.id ? (
              <>
                <span className="spinner" /> در حال ساخت...
              </>
            ) : (
              "خرید"
            )}
          </button>
        </article>
      ))}
    </div>
  );
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
