import { useMemo, useState } from "react";
import {
  api,
  adminApi,
  Transaction,
  TxFilter,
  TransactionsPage,
} from "../api";
import { useResource } from "../lib/useApi";
import { EmptyState, Skeleton } from "./ui";

const TYPE_FA: Record<string, string> = {
  topup: "شارژ",
  usage_debit: "مصرف",
  usage_credit: "درآمد",
  commission: "کارمزد",
  refund: "بازگشت",
  payout: "برداشت",
  adjustment: "تنظیم دستی",
};

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "topup", label: TYPE_FA.topup },
  { value: "usage_debit", label: TYPE_FA.usage_debit },
  { value: "usage_credit", label: TYPE_FA.usage_credit },
  { value: "commission", label: TYPE_FA.commission },
  { value: "refund", label: TYPE_FA.refund },
  { value: "payout", label: TYPE_FA.payout },
  { value: "adjustment", label: TYPE_FA.adjustment },
];

function formatAmount(raw: string): { sign: "+" | "-" | ""; abs: string } {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return { sign: "", abs: raw };
  const sign: "+" | "-" | "" = n > 0 ? "+" : n < 0 ? "-" : "";
  return {
    sign,
    abs: Math.abs(n).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 4,
    }),
  };
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("fa-IR", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function TxRow({ t }: { t: Transaction }) {
  const { sign, abs } = formatAmount(t.amount);
  const color =
    sign === "+" ? "var(--ok, #14a058)" : sign === "-" ? "var(--err, #d23)" : undefined;
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="title" style={{ fontSize: 14 }}>
            {TYPE_FA[t.type] ?? t.type}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            {formatDate(t.created_at)}
          </div>
          {t.note && (
            <div style={{ fontSize: 13, marginTop: 6 }}>{t.note}</div>
          )}
          {t.ref && (
            <div
              className="muted"
              style={{ fontSize: 11, marginTop: 4, direction: "ltr" }}
            >
              ref: <code>{t.ref}</code>
            </div>
          )}
        </div>
        <div
          style={{
            fontWeight: 700,
            fontSize: 15,
            color,
            direction: "ltr",
            whiteSpace: "nowrap",
          }}
        >
          {sign}
          {abs} {t.currency}
        </div>
      </div>
    </div>
  );
}

export type TransactionsListProps = {
  /** If set, fetches via admin endpoint for that user instead of /api/me. */
  adminUserId?: number;
};

export function TransactionsList({ adminUserId }: TransactionsListProps) {
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [direction, setDirection] = useState<"all" | "credit" | "debit">("all");
  const [from, setFrom] = useState<string>("");
  const [to, setTo] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const size = 20;

  const filter: TxFilter = useMemo(() => {
    const typeCsv = Array.from(selectedTypes).join(",");
    const f: TxFilter = { page, size };
    if (typeCsv) f.type = typeCsv;
    if (direction !== "all") f.direction = direction;
    if (from) f.from = new Date(from).toISOString();
    if (to) {
      // include the whole "to" day
      const d = new Date(to);
      d.setHours(23, 59, 59, 999);
      f.to = d.toISOString();
    }
    return f;
  }, [selectedTypes, direction, from, to, page]);

  const fetcher = () =>
    adminUserId !== undefined
      ? adminApi.listUserTransactions(adminUserId, filter)
      : api.listMyTransactions(filter);

  const { data, loading, error, refetch } = useResource<TransactionsPage>(
    fetcher,
    [adminUserId, JSON.stringify(filter)],
  );

  function toggleType(v: string) {
    setSelectedTypes((cur) => {
      const next = new Set(cur);
      if (next.has(v)) next.delete(v);
      else next.add(v);
      return next;
    });
    setPage(1);
  }

  function reset() {
    setSelectedTypes(new Set());
    setDirection("all");
    setFrom("");
    setTo("");
    setPage(1);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / size)) : 1;

  return (
    <div>
      <div className="card">
        <div className="title" style={{ fontSize: 14, marginBottom: 8 }}>
          فیلترها
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 6,
            marginBottom: 8,
          }}
        >
          {TYPE_OPTIONS.map((opt) => {
            const active = selectedTypes.has(opt.value);
            return (
              <button
                key={opt.value}
                type="button"
                className={"badge" + (active ? " badge-active" : "")}
                onClick={() => toggleType(opt.value)}
                style={{
                  border: active
                    ? "1px solid var(--accent, #2a73ff)"
                    : "1px solid var(--border, #ddd)",
                  background: active
                    ? "var(--accent, #2a73ff)"
                    : "transparent",
                  color: active ? "#fff" : "inherit",
                  cursor: "pointer",
                }}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            alignItems: "center",
          }}
        >
          <label style={{ fontSize: 12 }}>
            جهت:&nbsp;
            <select
              value={direction}
              onChange={(e) => {
                setDirection(e.target.value as "all" | "credit" | "debit");
                setPage(1);
              }}
            >
              <option value="all">همه</option>
              <option value="credit">واریز</option>
              <option value="debit">برداشت</option>
            </select>
          </label>
          <label style={{ fontSize: 12 }}>
            از:&nbsp;
            <input
              type="date"
              value={from}
              onChange={(e) => {
                setFrom(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label style={{ fontSize: 12 }}>
            تا:&nbsp;
            <input
              type="date"
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <button className="btn-ghost" type="button" onClick={reset}>
            ریست
          </button>
        </div>
      </div>

      {error && (
        <div className="alert alert-error">
          {error}{" "}
          <button
            className="btn-ghost"
            type="button"
            onClick={refetch}
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
      )}

      {loading && !data && (
        <div className="card">
          <Skeleton width="40%" height={14} />
          <div style={{ height: 8 }} />
          <Skeleton width="80%" height={12} />
        </div>
      )}

      {data && data.items.length === 0 && (
        <EmptyState emoji="🧾" title="تراکنشی یافت نشد" />
      )}

      {data &&
        data.items.length > 0 &&
        data.items.map((t) => <TxRow key={t.id} t={t} />)}

      {data && data.total > size && (
        <div
          className="row"
          style={{ justifyContent: "center", gap: 12, marginTop: 8 }}
        >
          <button
            type="button"
            className="btn-ghost"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            قبلی
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            {page} از {totalPages}
          </span>
          <button
            type="button"
            className="btn-ghost"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            بعدی
          </button>
        </div>
      )}
    </div>
  );
}
