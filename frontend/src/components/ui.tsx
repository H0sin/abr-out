import { ReactNode, useEffect } from "react";

export function Skeleton({
  width,
  height = 16,
  radius,
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className="skeleton"
      style={{
        width: width ?? "100%",
        height,
        borderRadius: radius,
        ...style,
      }}
    />
  );
}

export function Spinner() {
  return <span className="spinner" />;
}

export function EmptyState({
  emoji = "✨",
  title,
  hint,
  action,
}: {
  emoji?: string;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-emoji">{emoji}</div>
      <div className="empty-title">{title}</div>
      {hint && <div className="muted">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

const STATUS_FA: Record<string, string> = {
  active: "فعال",
  pending: "در انتظار تأیید",
  broken: "موقتاً ناپایدار",
  rejected: "رد شده",
  disabled: "غیرفعال",
  expired: "منقضی",
};

export function StatusBadge({ status }: { status: string }) {
  const label = STATUS_FA[status] ?? status;
  return <span className={`badge badge-${status}`}>{label}</span>;
}

export function PingPill({ ms }: { ms: number | null }) {
  if (ms === null || ms === undefined) {
    return <span className="ping-pill">— ms</span>;
  }
  const cls = ms < 80 ? "ping-good" : ms < 200 ? "ping-mid" : "ping-bad";
  return (
    <span className={`ping-pill ${cls}`}>
      <span className="num">{ms}</span> ms
    </span>
  );
}

/**
 * Compact "ping-in-a-circle" badge.
 *
 * Renders an SVG ring whose color smoothly interpolates between green
 * (good), orange (medium), and red (bad). Color thresholds match the
 * pill above so both widgets stay consistent.
 */
export function PingCircle({ ms }: { ms: number | null }) {
  const known = ms !== null && ms !== undefined;
  const tone = !known
    ? "ping-unknown"
    : ms < 80
      ? "ping-good"
      : ms < 200
        ? "ping-mid"
        : "ping-bad";
  return (
    <span className={`ping-circle ${tone}`} title="Ping (ms)">
      <span className="ping-circle-text">
        {known ? (
          <>
            <span className="num">{ms}</span>
            <span className="ping-circle-unit">ms</span>
          </>
        ) : (
          <span className="ping-circle-unit">—</span>
        )}
      </span>
    </span>
  );
}

/**
 * Stability % indicator. Currently a forward-compatible placeholder: when
 * the backend has not yet computed a value (``pct === null``), shows "—".
 */
export function StabilityPct({ pct }: { pct: number | null }) {
  if (pct === null || pct === undefined) {
    return (
      <span className="stat-pill stat-pill-muted" title="Stability (24h)">
        پایداری —
      </span>
    );
  }
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  const tone =
    clamped >= 90 ? "ping-good" : clamped >= 70 ? "ping-mid" : "ping-bad";
  return (
    <span className={`stat-pill ${tone}`} title="Stability (24h)">
      پایداری <span className="num">{clamped}</span>٪
    </span>
  );
}

export function SkeletonCard() {
  return (
    <div className="card">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <Skeleton width="60%" height={16} />
          <div style={{ height: 8 }} />
          <Skeleton width="40%" height={12} />
        </div>
        <Skeleton width={64} height={28} radius={8} />
      </div>
      <div style={{ height: 12 }} />
      <Skeleton height={42} radius={12} />
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);

    // Telegram WebApp: expand to full height + disable swipe-down-to-close
    // so the user can scroll the modal freely without triggering a close.
    const tg = window.Telegram?.WebApp as
      | {
          disableVerticalSwipes?: () => void;
          enableVerticalSwipes?: () => void;
          expand?: () => void;
        }
      | undefined;
    try {
      tg?.expand?.();
      tg?.disableVerticalSwipes?.();
    } catch {
      /* older WebApp versions don't expose these helpers */
    }

    // Remember + reset page scroll so the modal opens at the top, then
    // restore the previous scroll position on close.
    const prevScrollY = window.scrollY;
    window.scrollTo(0, 0);

    return () => {
      window.removeEventListener("keydown", onKey);
      window.scrollTo(0, prevScrollY);
      try {
        tg?.enableVerticalSwipes?.();
      } catch {
        /* noop */
      }
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true">
        <button
          type="button"
          className="modal-close"
          aria-label="بستن"
          onClick={onClose}
        >
          ×
        </button>
        {title && <h3>{title}</h3>}
        {children}
      </div>
    </div>
  );
}
