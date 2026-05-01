import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  api,
  AutoWithdrawConfig,
  AutoWithdrawInput,
  Withdrawal,
  WithdrawalQuote,
} from "../api";
import { useMe } from "../lib/MeContext";
import { useToast } from "../lib/toast";
import { Skeleton } from "../components/ui";
import { haptic } from "../lib/useTelegram";

const ADDR_RE = /^0x[a-fA-F0-9]{40}$/;
const STATUS_FA: Record<Withdrawal["status"], string> = {
  pending: "در صف ارسال",
  submitting: "در حال ارسال…",
  submitted: "ارسال‌شده — منتظر تأیید شبکه",
  confirmed: "تأیید شد ✅",
  failed: "ناموفق",
  refunded: "بازگشت داده شد",
};

function fmt(n: string | null | undefined, digits = 4): string {
  if (n === null || n === undefined) return "—";
  const v = parseFloat(n);
  if (!Number.isFinite(v)) return n;
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

export function Withdraw() {
  const { me, refresh } = useMe();
  const toast = useToast();

  // ---- manual form ----
  const [amount, setAmount] = useState("");
  const [address, setAddress] = useState("");
  const [quote, setQuote] = useState<WithdrawalQuote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeWid, setActiveWid] = useState<number | null>(null);
  const [activeStatus, setActiveStatus] = useState<Withdrawal | null>(null);

  // Debounced quote fetch.
  const quoteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (quoteTimer.current) clearTimeout(quoteTimer.current);
    setQuote(null);
    const a = parseFloat(amount);
    if (!Number.isFinite(a) || a <= 0) return;
    setQuoting(true);
    quoteTimer.current = setTimeout(async () => {
      try {
        const q = await api.getWithdrawalQuote(amount);
        setQuote(q);
      } catch (e) {
        if (e instanceof ApiError && e.status === 503) {
          toast.error("سرویس برداشت در حال حاضر در دسترس نیست.");
        }
      } finally {
        setQuoting(false);
      }
    }, 500);
    return () => {
      if (quoteTimer.current) clearTimeout(quoteTimer.current);
    };
  }, [amount, toast]);

  const balance = useMemo(() => parseFloat(me?.balance_usd ?? "0") || 0, [me]);
  const amountNum = parseFloat(amount);
  const addrOk = ADDR_RE.test(address.trim());
  const amountOk =
    Number.isFinite(amountNum) && amountNum > 0 && amountNum <= balance;
  const canSubmit =
    addrOk && amountOk && !!quote && parseFloat(quote.net_usdt) > 0 && !submitting;

  function fillAll() {
    haptic.light();
    setAmount(String(balance.toFixed(4)));
  }

  async function onSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const w = await api.createWithdrawal({
        amount_usd: amount,
        to_address: address.trim(),
      });
      toast.success("درخواست ثبت شد. در حال ارسال به شبکه…");
      haptic.success();
      setActiveWid(w.id);
      setActiveStatus(w);
      setAmount("");
      refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "خطا در ثبت درخواست";
      toast.error(msg);
      haptic.error();
    } finally {
      setSubmitting(false);
    }
  }

  // Poll status of an in-flight withdrawal every 5s.
  useEffect(() => {
    if (!activeWid) return;
    let stopped = false;
    const tick = async () => {
      try {
        const w = await api.getWithdrawal(activeWid);
        if (stopped) return;
        setActiveStatus(w);
        if (
          w.status === "confirmed" ||
          w.status === "failed" ||
          w.status === "refunded"
        ) {
          refresh();
          return;
        }
      } catch {
        /* keep polling */
      }
      if (!stopped) setTimeout(tick, 5000);
    };
    tick();
    return () => {
      stopped = true;
    };
  }, [activeWid, refresh]);

  return (
    <div>
      <h2>برداشت</h2>

      <div className="balance-hero">
        <div className="balance-label">موجودی شما</div>
        <div className="balance-value">
          <span style={{ fontSize: 24, marginInlineEnd: 4 }}>$</span>
          {fmt(me?.balance_usd ?? "0", 2)}
        </div>
        <div className="balance-sub">USDT روی شبکهٔ BSC (BEP-20)</div>
      </div>

      {/* ---- Manual withdraw ---- */}
      <div className="card">
        <div className="title">برداشت دستی</div>
        <p className="muted mt-1" style={{ fontSize: 12 }}>
          مبلغ به دلار وارد شود؛ کارمزد شبکه از همان مبلغ کسر و مابقی به صورت
          USDT-BEP20 به آدرس شما ارسال می‌شود.
        </p>

        <label className="field-label" style={{ marginTop: 12 }}>مبلغ (USD)</label>
        <div className="row" style={{ gap: 8 }}>
          <input
            className="input"
            inputMode="decimal"
            placeholder="مثلاً 10"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(",", "."))}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            className="btn btn-ghost"
            onClick={fillAll}
            disabled={balance <= 0}
          >
            همه موجودی
          </button>
        </div>

        <label className="field-label" style={{ marginTop: 12 }}>آدرس مقصد (BSC / BEP-20)</label>
        <input
          className="input"
          placeholder="0x…"
          value={address}
          onChange={(e) => setAddress(e.target.value.trim())}
          dir="ltr"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
        />
        {address && !addrOk && (
          <div className="alert alert-error mt-2">
            آدرس BSC نامعتبر است.
          </div>
        )}

        <div className="mt-3" style={{ fontSize: 13 }}>
          {quoting && (
            <div className="row" style={{ gap: 8 }}>
              <Skeleton width={140} height={14} />
            </div>
          )}
          {!quoting && quote && (
            <div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">کارمزد شبکه</span>
                <b style={{ direction: "ltr" }}>{fmt(quote.fee_usd, 4)}$</b>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">مبلغ دریافتی</span>
                <b style={{ direction: "ltr" }}>{fmt(quote.net_usdt, 4)} USDT</b>
              </div>
              {parseFloat(quote.net_usdt) <= 0 && (
                <div className="alert alert-error mt-2">
                  کارمزد شبکه از مبلغ برداشت بیشتر است؛ لطفاً مبلغ بزرگ‌تری وارد کنید.
                </div>
              )}
            </div>
          )}
        </div>

        <button
          type="button"
          className="btn btn-primary mt-3"
          disabled={!canSubmit}
          onClick={onSubmit}
        >
          {submitting ? "در حال ارسال…" : "تأیید برداشت"}
        </button>
      </div>

      {/* ---- In-progress / last status panel ---- */}
      {activeStatus && (
        <div className="card">
          <div className="title">وضعیت آخرین برداشت</div>
          <div className="row mt-2" style={{ justifyContent: "space-between" }}>
            <span className="muted">وضعیت</span>
            <b>{STATUS_FA[activeStatus.status]}</b>
          </div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">مبلغ</span>
            <span style={{ direction: "ltr" }}>
              {fmt(activeStatus.amount_usd, 4)}$ →{" "}
              {fmt(activeStatus.net_usdt, 4)} USDT
            </span>
          </div>
          {activeStatus.tx_hash && (
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Tx</span>
              <a
                href={`https://bscscan.com/tx/${activeStatus.tx_hash}`}
                target="_blank"
                rel="noreferrer"
                style={{
                  direction: "ltr",
                  fontFamily: "monospace",
                  fontSize: 12,
                }}
              >
                {activeStatus.tx_hash.slice(0, 10)}…
                {activeStatus.tx_hash.slice(-6)}
              </a>
            </div>
          )}
          {activeStatus.error_msg && (
            <div className="alert alert-error mt-2">{activeStatus.error_msg}</div>
          )}
        </div>
      )}

      <AutoWithdrawSection defaultAddress={address || undefined} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Auto-withdraw configuration block
// ---------------------------------------------------------------------------

function AutoWithdrawSection({ defaultAddress }: { defaultAddress?: string }) {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cfg, setCfg] = useState<AutoWithdrawConfig | null>(null);

  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState<"time" | "threshold">("time");
  const [intervalHours, setIntervalHours] = useState("24");
  const [threshold, setThreshold] = useState("10");
  const [policy, setPolicy] = useState<"full" | "fixed">("full");
  const [fixedAmount, setFixedAmount] = useState("10");
  const [address, setAddress] = useState(defaultAddress ?? "");

  useEffect(() => {
    let cancelled = false;
    api
      .getAutoWithdraw()
      .then((c) => {
        if (cancelled) return;
        setCfg(c);
        if (c) {
          setEnabled(c.enabled);
          setMode(c.mode);
          if (c.interval_hours) setIntervalHours(String(c.interval_hours));
          if (c.threshold_usd) setThreshold(c.threshold_usd);
          setPolicy(c.amount_policy);
          if (c.fixed_amount_usd) setFixedAmount(c.fixed_amount_usd);
          setAddress(c.to_address);
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  // If user just typed an address in the manual form and we don't have
  // a saved auto-config yet, prefill from there.
  useEffect(() => {
    if (!cfg && defaultAddress && !address) setAddress(defaultAddress);
  }, [defaultAddress, cfg, address]);

  const addrOk = ADDR_RE.test(address.trim());

  async function onSave() {
    if (!addrOk) {
      toast.error("آدرس مقصد نامعتبر است.");
      return;
    }
    const body: AutoWithdrawInput = {
      enabled,
      mode,
      amount_policy: policy,
      to_address: address.trim(),
      interval_hours: mode === "time" ? parseInt(intervalHours, 10) : null,
      threshold_usd: mode === "threshold" ? threshold : null,
      fixed_amount_usd: policy === "fixed" ? fixedAmount : null,
    };
    setSaving(true);
    try {
      const c = await api.saveAutoWithdraw(body);
      setCfg(c);
      toast.success(
        enabled ? "برداشت خودکار فعال شد." : "تنظیمات ذخیره شد.",
      );
      haptic.success();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "خطا در ذخیره تنظیمات";
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  }

  async function onDisable() {
    setSaving(true);
    try {
      const c = await api.disableAutoWithdraw();
      setCfg(c);
      setEnabled(false);
      toast.info("برداشت خودکار غیرفعال شد.");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "خطا";
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="card">
        <Skeleton width="40%" height={16} />
        <div style={{ height: 8 }} />
        <Skeleton height={48} radius={12} />
      </div>
    );
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="title">برداشت خودکار</div>
        <label className="row" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>{enabled ? "فعال" : "غیرفعال"}</span>
        </label>
      </div>
      <p className="muted mt-1" style={{ fontSize: 12 }}>
        می‌توانی به‌صورت زمانی یا بر اساس آستانهٔ موجودی، برداشت را خودکار کنی.
      </p>

      <div className="mt-3">
        <div className="field-label">حالت اجرا</div>
        <div className="row" style={{ gap: 12, marginTop: 4 }}>
          <label className="row" style={{ gap: 6 }}>
            <input
              type="radio"
              name="mode"
              checked={mode === "time"}
              onChange={() => setMode("time")}
            />
            <span>زمانی</span>
          </label>
          <label className="row" style={{ gap: 6 }}>
            <input
              type="radio"
              name="mode"
              checked={mode === "threshold"}
              onChange={() => setMode("threshold")}
            />
            <span>آستانه‌ای</span>
          </label>
        </div>

        {mode === "time" ? (
          <div className="mt-2">
            <div className="field-label">هر چند ساعت یک‌بار؟</div>
            <input
              className="input"
              inputMode="numeric"
              value={intervalHours}
              onChange={(e) =>
                setIntervalHours(e.target.value.replace(/[^0-9]/g, ""))
              }
              placeholder="24"
            />
          </div>
        ) : (
          <div className="mt-2">
            <div className="field-label">وقتی موجودی به این مقدار رسید (USD)</div>
            <input
              className="input"
              inputMode="decimal"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value.replace(",", "."))}
              placeholder="10"
            />
          </div>
        )}
      </div>

      <div className="mt-3">
        <div className="field-label">مبلغ هر اجرا</div>
        <div className="row" style={{ gap: 12, marginTop: 4 }}>
          <label className="row" style={{ gap: 6 }}>
            <input
              type="radio"
              name="policy"
              checked={policy === "full"}
              onChange={() => setPolicy("full")}
            />
            <span>همه موجودی</span>
          </label>
          <label className="row" style={{ gap: 6 }}>
            <input
              type="radio"
              name="policy"
              checked={policy === "fixed"}
              onChange={() => setPolicy("fixed")}
            />
            <span>مبلغ ثابت</span>
          </label>
        </div>
        {policy === "fixed" && (
          <input
            className="input mt-2"
            inputMode="decimal"
            value={fixedAmount}
            onChange={(e) => setFixedAmount(e.target.value.replace(",", "."))}
            placeholder="10"
          />
        )}
      </div>

      <div className="mt-3">
        <div className="field-label">آدرس مقصد (BSC / BEP-20)</div>
        <input
          className="input"
          placeholder="0x…"
          value={address}
          onChange={(e) => setAddress(e.target.value.trim())}
          dir="ltr"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
        />
        {address && !addrOk && (
          <div className="alert alert-error mt-2">آدرس BSC نامعتبر است.</div>
        )}
      </div>

      {cfg?.enabled && (
        <div className="mt-3" style={{ fontSize: 12 }} dir="rtl">
          {cfg.mode === "time" && cfg.next_run_at && (
            <div className="muted">
              اجرای بعدی: {new Date(cfg.next_run_at).toLocaleString("fa-IR")}
            </div>
          )}
          {cfg.mode === "threshold" && cfg.threshold_usd && (
            <div className="muted">
              منتظر رسیدن موجودی به {fmt(cfg.threshold_usd, 2)}$
            </div>
          )}
          {cfg.last_run_at && (
            <div className="muted">
              آخرین اجرا: {new Date(cfg.last_run_at).toLocaleString("fa-IR")}
            </div>
          )}
        </div>
      )}

      <div className="row mt-3" style={{ gap: 8 }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={saving}
          onClick={onSave}
          style={{ flex: 1 }}
        >
          {saving ? "…" : "ذخیره"}
        </button>
        {cfg?.enabled && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onDisable}
            disabled={saving}
          >
            غیرفعال‌سازی
          </button>
        )}
      </div>
    </div>
  );
}
