import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { adminApi, AdminUser } from "../../api";
import { EmptyState, Skeleton } from "../../components/ui";
import { useResource } from "../../lib/useApi";
import { useToast } from "../../lib/toast";

const TX_TYPES: { value: string; label: string }[] = [
  { value: "adjustment", label: "تنظیم دستی" },
  { value: "topup", label: "شارژ" },
  { value: "refund", label: "بازگشت وجه" },
  { value: "payout", label: "برداشت" },
];

function fmtUsd(raw: string): string {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return raw;
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function AdminUserDetail() {
  const { id } = useParams<{ id: string }>();
  const userId = Number(id);
  const { data, loading, error, refetch, setData } = useResource<AdminUser>(
    () => adminApi.getUser(userId),
    [userId],
  );
  const toast = useToast();

  const [adjAmount, setAdjAmount] = useState<string>("");
  const [adjDirection, setAdjDirection] = useState<"add" | "sub">("add");
  const [adjType, setAdjType] = useState<string>("adjustment");
  const [adjNote, setAdjNote] = useState<string>("");
  const [adjBusy, setAdjBusy] = useState(false);

  const [dmText, setDmText] = useState<string>("");
  const [dmBusy, setDmBusy] = useState(false);

  const [blockBusy, setBlockBusy] = useState(false);

  if (error) {
    return (
      <div className="alert alert-error">
        {error}{" "}
        <button
          type="button"
          onClick={refetch}
          className="btn-ghost"
          style={{
            background: "none",
            border: "none",
            padding: 0,
            color: "inherit",
            textDecoration: "underline",
          }}
        >
          تلاش مجدد
        </button>
      </div>
    );
  }
  if (loading && !data) {
    return (
      <div className="card">
        <Skeleton width="60%" />
        <div style={{ height: 8 }} />
        <Skeleton width="80%" />
      </div>
    );
  }
  if (!data) return <EmptyState emoji="👤" title="کاربر یافت نشد" />;

  async function toggleBlock() {
    if (!data) return;
    setBlockBusy(true);
    try {
      const next = !data.is_blocked;
      const u = await adminApi.setBlocked(userId, next);
      setData(() => u);
      toast.success(next ? "کاربر بلاک شد" : "کاربر آنبلاک شد");
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setBlockBusy(false);
    }
  }

  async function submitAdjust(e: React.FormEvent) {
    e.preventDefault();
    if (!adjAmount || !adjNote || adjNote.trim().length < 2) {
      toast.error("مبلغ و علت اجباری است");
      return;
    }
    setAdjBusy(true);
    try {
      const n = parseFloat(adjAmount.replace(",", "."));
      if (!Number.isFinite(n) || n <= 0) {
        toast.error("مبلغ نامعتبر");
        return;
      }
      const signed = adjDirection === "add" ? n : -n;
      await adminApi.addTransaction(userId, {
        amount: signed,
        type: adjType,
        note: adjNote.trim(),
      });
      toast.success("تراکنش ثبت شد");
      setAdjAmount("");
      setAdjNote("");
      refetch();
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setAdjBusy(false);
    }
  }

  async function submitDM(e: React.FormEvent) {
    e.preventDefault();
    if (!dmText.trim()) return;
    setDmBusy(true);
    try {
      const r = await adminApi.sendDM(userId, dmText.trim());
      if (r.ok) {
        toast.success("ارسال شد");
        setDmText("");
      } else {
        toast.error("ارسال نشد (ربات بلاک شده؟)");
      }
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setDmBusy(false);
    }
  }

  return (
    <div>
      <h2>{data.username ? `@${data.username}` : `ID: ${data.telegram_id}`}</h2>

      <div className="card">
        <div className="row">
          <div style={{ flex: 1 }}>
            <div className="title" style={{ fontSize: 14 }}>
              موجودی
            </div>
            <div style={{ direction: "ltr", fontSize: 24, fontWeight: 700 }}>
              ${fmtUsd(data.balance_usd)}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4, direction: "ltr" }}>
              {data.telegram_id} · {data.configs_count} کانفیگ · {data.listings_count} لیستینگ
            </div>
          </div>
          <div>
            {data.is_blocked ? (
              <span className="badge badge-rejected">مسدود</span>
            ) : (
              <span className="badge badge-active">فعال</span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          <button
            type="button"
            className={"btn " + (data.is_blocked ? "btn-primary" : "btn-danger")}
            disabled={blockBusy}
            onClick={toggleBlock}
          >
            {data.is_blocked ? "🔓 آنبلاک" : "🚫 بلاک"}
          </button>
          <Link
            to={`/admin/users/${data.telegram_id}/transactions`}
            className="btn-ghost"
            style={{ textDecoration: "none" }}
          >
            📜 تراکنش‌ها
          </Link>
        </div>
      </div>

      <div className="card">
        <div className="title">تنظیم موجودی</div>
        <form onSubmit={submitAdjust} style={{ marginTop: 8 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select
              value={adjDirection}
              onChange={(e) => setAdjDirection(e.target.value as "add" | "sub")}
            >
              <option value="add">➕ افزایش</option>
              <option value="sub">➖ کاهش</option>
            </select>
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="مبلغ ($)"
              value={adjAmount}
              onChange={(e) => setAdjAmount(e.target.value)}
              style={{ width: 120 }}
            />
            <select value={adjType} onChange={(e) => setAdjType(e.target.value)}>
              {TX_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <textarea
            placeholder="علت (اجباری)"
            value={adjNote}
            onChange={(e) => setAdjNote(e.target.value)}
            rows={2}
            style={{ width: "100%", marginTop: 8 }}
          />
          <button type="submit" className="btn btn-primary mt-2" disabled={adjBusy}>
            {adjBusy ? "..." : "ثبت"}
          </button>
        </form>
      </div>

      <div className="card">
        <div className="title">پیام مستقیم</div>
        <form onSubmit={submitDM} style={{ marginTop: 8 }}>
          <textarea
            placeholder="متن پیام"
            value={dmText}
            onChange={(e) => setDmText(e.target.value)}
            rows={3}
            style={{ width: "100%" }}
          />
          <button type="submit" className="btn btn-primary mt-2" disabled={dmBusy}>
            {dmBusy ? "..." : "ارسال"}
          </button>
        </form>
      </div>
    </div>
  );
}
