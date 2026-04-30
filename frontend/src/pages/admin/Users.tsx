import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { adminApi, AdminUserFilter } from "../../api";
import { EmptyState, Skeleton } from "../../components/ui";
import { useResource } from "../../lib/useApi";

function fmtUsd(raw: string): string {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return raw;
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function AdminUsers() {
  const [q, setQ] = useState("");
  const [blocked, setBlocked] = useState<"all" | "yes" | "no">("all");
  const [sort, setSort] = useState<"created_at" | "balance" | "username" | "telegram_id">(
    "created_at",
  );
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const size = 20;

  const filter: AdminUserFilter = useMemo(
    () => ({ q: q || undefined, blocked, sort, order, page, size }),
    [q, blocked, sort, order, page],
  );

  const { data, loading, error, refetch } = useResource(
    () => adminApi.listUsers(filter),
    [JSON.stringify(filter)],
  );

  const totalPages = data ? Math.max(1, Math.ceil(data.total / size)) : 1;

  return (
    <div>
      <h2>کاربران</h2>

      <div className="card">
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            alignItems: "center",
          }}
        >
          <input
            type="text"
            placeholder="جستجو (یوزرنیم یا آی‌دی)"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
            style={{ flex: 1, minWidth: 160 }}
          />
          <select
            value={blocked}
            onChange={(e) => {
              setBlocked(e.target.value as "all" | "yes" | "no");
              setPage(1);
            }}
          >
            <option value="all">همه</option>
            <option value="no">فعال</option>
            <option value="yes">مسدود</option>
          </select>
          <select
            value={sort}
            onChange={(e) =>
              setSort(
                e.target.value as
                  | "created_at"
                  | "balance"
                  | "username"
                  | "telegram_id",
              )
            }
          >
            <option value="created_at">تاریخ ثبت‌نام</option>
            <option value="balance">موجودی</option>
            <option value="username">نام کاربری</option>
            <option value="telegram_id">آی‌دی</option>
          </select>
          <select
            value={order}
            onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
          >
            <option value="desc">نزولی</option>
            <option value="asc">صعودی</option>
          </select>
        </div>
      </div>

      {error && (
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
      )}

      {loading && !data && (
        <div className="card">
          <Skeleton width="60%" />
          <div style={{ height: 8 }} />
          <Skeleton width="80%" />
        </div>
      )}

      {data && data.items.length === 0 && (
        <EmptyState emoji="👤" title="کاربری یافت نشد" />
      )}

      {data &&
        data.items.map((u) => (
          <Link
            key={u.telegram_id}
            to={`/admin/users/${u.telegram_id}`}
            style={{ textDecoration: "none", color: "inherit" }}
          >
            <div className="card" style={{ padding: 12 }}>
              <div className="row" style={{ alignItems: "center" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="title" style={{ fontSize: 14 }}>
                    {u.username ? `@${u.username}` : `ID: ${u.telegram_id}`}{" "}
                    {u.is_blocked && (
                      <span className="badge" style={{ background: "#d23", color: "#fff" }}>
                        مسدود
                      </span>
                    )}
                  </div>
                  <div className="muted" style={{ fontSize: 11, direction: "ltr" }}>
                    {u.telegram_id} · {u.configs_count} کانفیگ · {u.listings_count} لیستینگ
                  </div>
                </div>
                <div style={{ direction: "ltr", fontWeight: 700 }}>
                  ${fmtUsd(u.balance_usd)}
                </div>
              </div>
            </div>
          </Link>
        ))}

      {data && data.total > size && (
        <div className="row" style={{ justifyContent: "center", gap: 12, marginTop: 8 }}>
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
