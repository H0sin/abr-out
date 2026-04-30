import { useState } from "react";
import { adminApi, SupportEntry } from "../../api";
import { EmptyState, Skeleton } from "../../components/ui";
import { useResource } from "../../lib/useApi";
import { useToast } from "../../lib/toast";

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("fa-IR", {
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

function ReplyForm({
  entry,
  onSent,
}: {
  entry: SupportEntry;
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      const r = await adminApi.replySupport(entry.id, text.trim());
      if (r.ok) {
        toast.success("ارسال شد");
        setText("");
        onSent();
      } else {
        toast.error("ارسال نشد");
      }
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} style={{ marginTop: 8 }}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="پاسخ شما..."
        style={{ width: "100%" }}
      />
      <button type="submit" className="btn btn-primary mt-2" disabled={busy}>
        {busy ? "..." : "ارسال پاسخ"}
      </button>
    </form>
  );
}

export function AdminSupport() {
  const [onlyUnanswered, setOnlyUnanswered] = useState(true);
  const [page, setPage] = useState(1);
  const size = 20;

  const { data, loading, error, refetch } = useResource(
    () => adminApi.listSupport({ only_unanswered: onlyUnanswered, page, size }),
    [onlyUnanswered, page],
  );

  const totalPages = data ? Math.max(1, Math.ceil(data.total / size)) : 1;

  return (
    <div>
      <h2>پشتیبانی</h2>
      <div className="card">
        <label style={{ fontSize: 13 }}>
          <input
            type="checkbox"
            checked={onlyUnanswered}
            onChange={(e) => {
              setOnlyUnanswered(e.target.checked);
              setPage(1);
            }}
          />{" "}
          فقط بی‌پاسخ‌ها
        </label>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {loading && !data && (
        <div className="card">
          <Skeleton width="60%" />
          <div style={{ height: 8 }} />
          <Skeleton width="80%" />
        </div>
      )}
      {data && data.items.length === 0 && (
        <EmptyState emoji="📨" title="پیامی نیست" />
      )}

      {data &&
        data.items.map((e) => (
          <div key={e.id} className="card">
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="title" style={{ fontSize: 14 }}>
                  {e.username ? `@${e.username}` : `ID: ${e.user_id}`}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {formatDate(e.created_at)}
                </div>
                <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{e.text}</div>
              </div>
            </div>
            <ReplyForm entry={e} onSent={refetch} />
          </div>
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
