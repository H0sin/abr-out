import { FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError, Listing, api } from "../api";
import { EmptyState, Modal, SkeletonCard, StatusBadge } from "../components/ui";
import { useResource } from "../lib/useApi";
import { useMe } from "../lib/MeContext";
import { useToast } from "../lib/toast";
import { haptic, useMainButton } from "../lib/useTelegram";

const MAX_LISTINGS = 5;

const HOST_RE =
  /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;

function fmtUsd(raw: string | number): string {
  const n = typeof raw === "number" ? raw : parseFloat(raw);
  if (!Number.isFinite(n)) return String(raw);
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  });
}

type Errors = Partial<Record<"title" | "host" | "port" | "price", string>>;

export function Sell() {
  const { data: mine, loading, error, refetch } = useResource(
    () => api.listMyListings(),
  );
  const { me } = useMe();
  const tunnelTarget = me?.tunnel_target_host ?? "";
  const [title, setTitle] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [price, setPrice] = useState("");
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Listing | null>(null);
  // Quality-gate watch: after submit the listing starts in ``pending`` and
  // the worker promotes it to ``active`` only when the Iran-side prober
  // posts a successful ping sample. We poll /listings/mine every 10s until
  // either the listing turns active (success), disappears (rejected), or
  // the 5-minute deadline elapses.
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [pendingDeadline, setPendingDeadline] = useState<number | null>(null);
  const toast = useToast();

  const usedCount = mine?.length ?? 0;
  const atCap = usedCount >= MAX_LISTINGS;

  const errors = useMemo<Errors>(() => {
    const e: Errors = {};
    if (!title.trim()) e.title = "عنوان الزامی است";
    else if (title.length > 100) e.title = "حداکثر ۱۰۰ کاراکتر";
    else if (!/^[A-Za-z0-9 ._-]+$/.test(title.trim()))
      e.title = "فقط حروف انگلیسی، عدد، فاصله، نقطه و خط تیره";
    if (!host.trim()) e.host = "IP الزامی است";
    else if (!HOST_RE.test(host.trim())) e.host = "فقط IPv4 معتبر (مثلاً 1.2.3.4)";
    const p = parseInt(port, 10);
    if (!port) e.port = "پورت الزامی است";
    else if (!Number.isFinite(p) || p < 1 || p > 65535)
      e.port = "بین ۱ تا ۶۵۵۳۵";
    const pr = parseFloat(price);
    if (!price) e.price = "قیمت الزامی است";
    else if (!Number.isFinite(pr) || pr <= 0) e.price = "بزرگ‌تر از صفر";
    return e;
  }, [title, host, port, price]);

  const valid = Object.keys(errors).length === 0;

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    setTouched({ title: true, host: true, port: true, price: true });
    if (!valid || busy) return;
    if (atCap) {
      toast.error(`حداکثر ${MAX_LISTINGS} اوت‌باند مجاز است، یکی را حذف کنید`);
      return;
    }
    setBusy(true);
    try {
      const created = await api.createListing({
        title: title.trim(),
        iran_host: host.trim(),
        port: parseInt(port, 10),
        price_per_gb_usd: parseFloat(price),
      });
      haptic.success();
      if (created.status === "pending") {
        setPendingId(created.id);
        // Match the backend deadline (listing_quality_gate_minutes, default 5).
        // We give the UI a small extra grace window so the final toast lands
        // even if the worker tick is slightly delayed.
        setPendingDeadline(Date.now() + 5 * 60 * 1000 + 30_000);
        toast.success("اوت‌باند ثبت شد و در انتظار بررسی کیفیت است.");
      } else {
        toast.success("اوت‌باند با موفقیت ثبت و فعال شد.");
      }
      setTitle("");
      setHost("");
      setPort("");
      setPrice("");
      setTouched({});
      refetch();
    } catch (err) {
      haptic.error();
      toast.error(err instanceof ApiError ? err.message : "خطا در ثبت");
    } finally {
      setBusy(false);
    }
  }

  // Drive submit through Telegram MainButton for native feel.
  useMainButton({
    text: busy ? "در حال ارسال..." : "ثبت اوت‌باند",
    onClick: () => submit(),
    loading: busy,
    disabled: !valid || busy || atCap,
  });

  // Hide MainButton if user navigates away mid-flight (covered by hook cleanup).
  useEffect(() => () => undefined, []);

  // Poll /listings/mine every 10s while a pending listing is being verified.
  // Three terminal states: promoted to active (success), disappeared from
  // ``mine`` (the quality-gate worker hard-deleted it), or deadline elapsed
  // (the worker is lagging — we stop polling and let the seller refresh
  // manually).
  useEffect(() => {
    if (pendingId == null) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const list = await api.listMyListings();
        if (cancelled) return;
        const row = list.find((l) => l.id === pendingId);
        if (!row) {
          setPendingId(null);
          setPendingDeadline(null);
          haptic.error();
          toast.error(
            "اوت‌باند تأیید نشد — سرور ایران پاسخگو نبود و ثبت لغو شد.",
          );
          refetch();
          return;
        }
        if (row.status === "active") {
          setPendingId(null);
          setPendingDeadline(null);
          haptic.success();
          toast.success("کیفیت تأیید شد، اوت‌باند فعال است.");
          refetch();
          return;
        }
        if (pendingDeadline != null && Date.now() > pendingDeadline) {
          setPendingId(null);
          setPendingDeadline(null);
          toast.error(
            "بررسی کیفیت طولانی شد. لطفاً صفحه را به‌روز کنید تا وضعیت نهایی نمایش داده شود.",
          );
          refetch();
          return;
        }
      } catch {
        // Transient network error — keep polling.
      }
    };

    void tick();
    const id = window.setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pendingId, pendingDeadline, refetch, toast]);

  return (
    <div>
      <h2>فروش اوت‌باند</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        اوت‌باند ایران خود را اضافه کنید؛ بلافاصله در مارکت قابل خرید است.
      </p>
      <p className="muted" style={{ marginTop: 2, fontSize: 12 }}>
        ظرفیت ثبت: <span className="num">{usedCount}</span> / {MAX_LISTINGS}
        {atCap && " — برای ثبت جدید یکی از اوت‌باندها را حذف کنید"}
      </p>

      {pendingId != null && (
        <div
          className="card mt-3"
          style={{ borderLeft: "3px solid var(--warn, #d4a017)" }}
        >
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span className="spinner" />
            <div style={{ flex: 1 }}>
              <div className="title">در انتظار بررسی کیفیت</div>
              <div className="muted" style={{ marginTop: 4, fontSize: 13 }}>
                اوت‌باند شما ثبت شد و در حال تست از سمت سرور ایران است. تا
                چند دقیقه دیگر در صورت اتصال موفق به‌صورت خودکار تأیید
                می‌شود؛ در غیر این صورت ثبت لغو خواهد شد.
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="card mt-3" style={{ borderLeft: "3px solid var(--accent, #2ea3a3)" }}>
        <div className="title">راهنمای فروشنده</div>
        <ol className="muted" style={{ paddingInlineStart: 18, marginTop: 8, lineHeight: 1.9 }}>
          <li>
            وارد پنل ۳x-ui (ثنایی) ایران خودت شو و یک Inbound جدید بساز.
          </li>
          <li>
            <b>Protocol</b> را روی <code>tunnel</code> بگذار.
          </li>
          <li>
            <b>Port</b> همان پورتی است که در فرم زیر وارد می‌کنی (مثلاً{" "}
            <code dir="ltr">{port || "443"}</code>). همین پورت روی IP ایرانی‌ت
            باز خواهد شد و کانفیگ مشتری به آن وصل می‌شود.
          </li>
          <li>
            <b>Target Address</b> را برابر این آدرس بگذار:{" "}
            <code dir="ltr" className="copyable">
              {tunnelTarget || "—"}
            </code>
          </li>
          <li>
            <b>Destination Port</b> را هم برابر همان پورت بالا بگذار (مثلاً{" "}
            <code dir="ltr">{port || "443"}</code>).
          </li>
          <li>
            هاست/IP ایران در فرم زیر باید همان IP عمومی پنل ایرانت باشد —
            کانفیگ مشتری مستقیم به این IP و پورت وصل می‌شود و از تانل به سرور
            خارج ما منتقل می‌گردد.
          </li>
        </ol>
      </div>

      <form onSubmit={submit} className="card mt-3" noValidate>
        <Field
          label="عنوان"
          error={touched.title ? errors.title : undefined}
        >
          <input
            className={"input" + (touched.title && errors.title ? " invalid" : "")}
            type="text"
            dir="ltr"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, title: true }))}
            maxLength={100}
            placeholder="e.g. Tehran-MCI"
          />
        </Field>

        <Field label="IP ایران" error={touched.host ? errors.host : undefined}>
          <input
            className={"input" + (touched.host && errors.host ? " invalid" : "")}
            type="text"
            inputMode="decimal"
            dir="ltr"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            value={host}
            onChange={(e) => setHost(e.target.value.replace(/[^0-9.]/g, ""))}
            onBlur={() => setTouched((t) => ({ ...t, host: true }))}
            placeholder="1.2.3.4"
            maxLength={15}
          />
        </Field>

        <div className="row gap-3" style={{ alignItems: "flex-start" }}>
          <Field
            label="پورت"
            error={touched.port ? errors.port : undefined}
            className="flex-1"
          >
            <input
              className={"input" + (touched.port && errors.port ? " invalid" : "")}
              type="text"
              inputMode="numeric"
              dir="ltr"
              pattern="[0-9]*"
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
              onBlur={() => setTouched((t) => ({ ...t, port: true }))}
              placeholder="443"
            />
          </Field>
          <Field
            label="قیمت هر GB ($)"
            error={touched.price ? errors.price : undefined}
            className="flex-1"
          >
            <input
              className={"input" + (touched.price && errors.price ? " invalid" : "")}
              type="text"
              inputMode="decimal"
              dir="ltr"
              value={price}
              onChange={(e) =>
                setPrice(e.target.value.replace(/[^\d.]/g, ""))
              }
              onBlur={() => setTouched((t) => ({ ...t, price: true }))}
              placeholder="0.50"
            />
          </Field>
        </div>

        <button
          type="submit"
          className="btn btn-primary mt-2"
          disabled={!valid || busy || atCap}
        >
          {busy ? (
            <>
              <span className="spinner" /> در حال ارسال...
            </>
          ) : atCap ? (
            "ظرفیت تکمیل است"
          ) : (
            "ثبت اوت‌باند"
          )}
        </button>
      </form>

      <h3 className="mt-6">اوت‌باندهای من</h3>
      {error && <div className="alert alert-error mt-2">{error}</div>}
      {loading && !mine && (
        <>
          <SkeletonCard />
          <SkeletonCard />
        </>
      )}
      {mine && mine.length === 0 && (
        <EmptyState emoji="🏷" title="هنوز اوت‌باندی ثبت نکرده‌اید" />
      )}
      {mine?.map((l: Listing) => (
        <article key={l.id} className="card">
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="title">{l.title}</div>
              <div
                className="muted"
                style={{ direction: "ltr", textAlign: "right", marginTop: 2 }}
              >
                {l.iran_host}:{l.port}
                <span style={{ margin: "0 6px" }}>•</span>
                {fmtUsd(l.price_per_gb_usd)}$/GB
              </div>
            </div>
            <StatusBadge status={l.status} />
          </div>
          {l.status === "broken" && (
            <div
              className="muted mt-2"
              style={{ fontSize: 12, lineHeight: 1.7 }}
            >
              اوت‌باند موقتاً ناپایدار است و از مارکت پنهان شده. هر ۱۰
              دقیقه به‌صورت خودکار دوباره بررسی می‌شود؛ پس از دو پینگ
              موفق پیاپی به‌طور خودکار به فهرست خرید بازمی‌گردد.
            </div>
          )}
          <div className="muted mt-2">
            فروش: <span className="num">{l.sales_count}</span>
          </div>
          <ListingActions
            listing={l}
            onEdit={() => setEditing(l)}
            onChanged={refetch}
          />
        </article>
      ))}

      <EditListingModal
        listing={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          refetch();
        }}
      />
    </div>
  );
}

function Field({
  label,
  error,
  className,
  children,
}: {
  label: string;
  error?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={"field " + (className ?? "")}>
      <label className="field-label">{label}</label>
      {children}
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}

function ListingActions({
  listing,
  onEdit,
  onChanged,
}: {
  listing: Listing;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"" | "toggle" | "delete">("");
  const toast = useToast();
  // ``broken`` is a worker-managed transient state — for the seller's
  // purposes it behaves like ``active`` (re-disable hides it from the
  // marketplace permanently until they re-enable). ``pending`` shows no
  // toggle since the quality gate owns that transition.
  const canDisable =
    listing.status === "active" || listing.status === "broken";
  const isDeleted = listing.status === "deleted";
  const isPending = listing.status === "pending";

  if (isDeleted) return null;

  async function toggle() {
    if (busy) return;
    setBusy("toggle");
    try {
      if (canDisable) await api.disableListing(listing.id);
      else await api.enableListing(listing.id);
      haptic.success();
      toast.success(canDisable ? "اوت‌باند غیرفعال شد" : "اوت‌باند فعال شد");
      onChanged();
    } catch (e) {
      haptic.error();
      toast.error(e instanceof ApiError ? e.message : "خطا");
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    if (busy) return;
    if (
      !window.confirm(
        "آیا از حذف این اوت‌باند مطمئن هستید؟ تمام کانفیگ‌های خریداران حذف خواهند شد و به آن‌ها اطلاع داده می‌شود.",
      )
    )
      return;
    setBusy("delete");
    try {
      await api.deleteListing(listing.id);
      haptic.success();
      toast.success("اوت‌باند حذف شد");
      onChanged();
    } catch (e) {
      haptic.error();
      toast.error(e instanceof ApiError ? e.message : "خطا");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="row gap-2 mt-2">
      <button
        className="btn btn-secondary"
        style={{ flex: 1 }}
        onClick={onEdit}
        disabled={!!busy}
      >
        ویرایش
      </button>
      <button
        className="btn btn-secondary"
        style={{ flex: 1 }}
        onClick={toggle}
        disabled={!!busy || isPending}
        title={isPending ? "در حال بررسی کیفیت" : undefined}
      >
        {busy === "toggle"
          ? "..."
          : canDisable
            ? "غیرفعال‌سازی"
            : "فعال‌سازی"}
      </button>
      <button
        className="btn btn-danger"
        style={{ flex: 1 }}
        onClick={remove}
        disabled={!!busy}
      >
        {busy === "delete" ? "..." : "حذف"}
      </button>
    </div>
  );
}

function EditListingModal({
  listing,
  onClose,
  onSaved,
}: {
  listing: Listing | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState("");
  const [host, setHost] = useState("");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (listing) {
      setTitle(listing.title ?? "");
      setHost(listing.iran_host ?? "");
      setPrice(String(listing.price_per_gb_usd));
    }
  }, [listing]);

  if (!listing) return null;

  const titleValid =
    title.trim().length >= 2 && /^[A-Za-z0-9 ._-]+$/.test(title.trim());
  const hostValid = HOST_RE.test(host.trim());
  const priceNum = parseFloat(price);
  const priceValid = Number.isFinite(priceNum) && priceNum > 0;
  const valid = titleValid && hostValid && priceValid;

  async function save() {
    if (!listing || !valid || busy) return;
    setBusy(true);
    try {
      const body: {
        title?: string;
        iran_host?: string;
        price_per_gb_usd?: number;
      } = {};
      if (title.trim() !== (listing.title ?? "")) body.title = title.trim();
      if (host.trim() !== (listing.iran_host ?? "")) body.iran_host = host.trim();
      if (priceNum !== parseFloat(String(listing.price_per_gb_usd)))
        body.price_per_gb_usd = priceNum;
      if (Object.keys(body).length === 0) {
        onClose();
        return;
      }
      await api.patchListing(listing.id, body);
      haptic.success();
      toast.success("ذخیره شد");
      onSaved();
    } catch (e) {
      haptic.error();
      toast.error(e instanceof ApiError ? e.message : "خطا در ذخیره");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={listing !== null}
      onClose={onClose}
      title={`ویرایش اوت‌باند #${listing.id}`}
    >
      <div className="alert alert-info">
        پورت قابل تغییر نیست. تغییر IP و قیمت برای کانفیگ‌های فعال خریداران
        اطلاع‌رسانی می‌شود.
      </div>
      <div className="field">
        <label className="field-label">عنوان</label>
        <input
          className="input"
          dir="ltr"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={100}
        />
      </div>
      <div className="field">
        <label className="field-label">IP ایران</label>
        <input
          className="input"
          dir="ltr"
          value={host}
          onChange={(e) => setHost(e.target.value.replace(/[^0-9.]/g, ""))}
          maxLength={15}
        />
      </div>
      <div className="field">
        <label className="field-label">پورت (غیرقابل تغییر)</label>
        <input
          className="input"
          dir="ltr"
          value={String(listing.port ?? "")}
          disabled
          readOnly
        />
      </div>
      <div className="field">
        <label className="field-label">قیمت هر GB ($)</label>
        <input
          className="input"
          dir="ltr"
          inputMode="decimal"
          value={price}
          onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ""))}
        />
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose} disabled={busy}>
          انصراف
        </button>
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={!valid || busy}
        >
          {busy ? "در حال ذخیره..." : "ذخیره"}
        </button>
      </div>
    </Modal>
  );
}
