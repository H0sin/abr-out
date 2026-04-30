import { useEffect, useRef, useState } from "react";
import { adminApi, Audience, BroadcastJob } from "../../api";
import { useToast } from "../../lib/toast";

export function AdminBroadcast() {
  const [text, setText] = useState("");
  const [kind, setKind] = useState<Audience["kind"]>("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [preview, setPreview] = useState<number | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [job, setJob] = useState<BroadcastJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<number | null>(null);
  const toast = useToast();

  function buildAudience(): Audience {
    const a: Audience = { kind };
    if (kind === "date_range") {
      if (from) a.from = new Date(from).toISOString();
      if (to) {
        const d = new Date(to);
        d.setHours(23, 59, 59, 999);
        a.to = d.toISOString();
      }
    }
    return a;
  }

  async function doPreview() {
    setPreviewing(true);
    try {
      const r = await adminApi.broadcastPreview(buildAudience());
      setPreview(r.count);
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setPreviewing(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setSubmitting(true);
    try {
      const j = await adminApi.broadcast(text.trim(), buildAudience());
      setJob(j);
      toast.success(`در صف قرار گرفت (${j.total} گیرنده)`);
    } catch (e: any) {
      toast.error(e?.message || "خطا");
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = window.setInterval(async () => {
      try {
        const j = await adminApi.getBroadcast(job.id);
        setJob(j);
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [job?.id, job?.status]);

  return (
    <div>
      <h2>پیام همگانی</h2>
      <form onSubmit={submit}>
        <div className="card">
          <div className="title">مخاطبان</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            <select value={kind} onChange={(e) => setKind(e.target.value as Audience["kind"])}>
              <option value="all">همه کاربران</option>
              <option value="buyers">فقط خریداران</option>
              <option value="sellers">فقط فروشندگان</option>
              <option value="date_range">بازه ثبت‌نام</option>
            </select>
            {kind === "date_range" && (
              <>
                <label style={{ fontSize: 12 }}>
                  از:&nbsp;
                  <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
                </label>
                <label style={{ fontSize: 12 }}>
                  تا:&nbsp;
                  <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
                </label>
              </>
            )}
            <button
              type="button"
              className="btn-ghost"
              onClick={doPreview}
              disabled={previewing}
            >
              پیش‌نمایش تعداد
            </button>
            {preview !== null && (
              <span className="badge">{preview} گیرنده</span>
            )}
          </div>
        </div>

        <div className="card">
          <div className="title">متن پیام (HTML پشتیبانی می‌شود)</div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            style={{ width: "100%", marginTop: 8 }}
            placeholder="..."
          />
          <button
            type="submit"
            className="btn btn-primary mt-2"
            disabled={submitting || !text.trim()}
          >
            {submitting ? "..." : "ارسال"}
          </button>
        </div>
      </form>

      {job && (
        <div className="card">
          <div className="title">وضعیت ارسال #{job.id}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            وضعیت: <b>{job.status}</b> · کل: {job.total} · موفق: {job.sent} · ناموفق:{" "}
            {job.failed}
          </div>
        </div>
      )}
    </div>
  );
}
