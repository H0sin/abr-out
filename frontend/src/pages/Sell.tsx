import { FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError, Listing, api } from "../api";
import { EmptyState, SkeletonCard, StatusBadge } from "../components/ui";
import { useResource } from "../lib/useApi";
import { useMe } from "../lib/MeContext";
import { useToast } from "../lib/toast";
import { haptic, useMainButton } from "../lib/useTelegram";

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
  const toast = useToast();

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
    setBusy(true);
    try {
      await api.createListing({
        title: title.trim(),
        iran_host: host.trim(),
        port: parseInt(port, 10),
        price_per_gb_usd: parseFloat(price),
      });
      haptic.success();
      toast.success("اوت‌باند با موفقیت ثبت و فعال شد.");
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
    disabled: !valid || busy,
  });

  // Hide MainButton if user navigates away mid-flight (covered by hook cleanup).
  useEffect(() => () => undefined, []);

  return (
    <div>
      <h2>فروش اوت‌باند</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        اوت‌باند ایران خود را اضافه کنید؛ بلافاصله در مارکت قابل خرید است.
      </p>

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
          disabled={!valid || busy}
        >
          {busy ? (
            <>
              <span className="spinner" /> در حال ارسال...
            </>
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
          <div className="muted mt-2">
            فروش: <span className="num">{l.sales_count}</span>
          </div>
        </article>
      ))}
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
