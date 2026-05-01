import { Link, useLocation } from "react-router-dom";

export function AdminHome() {
  const cards = [
    { to: "/admin/users", emoji: "👥", title: "کاربران", desc: "لیست، فیلتر، مدیریت" },
    { to: "/admin/wallet", emoji: "💼", title: "کیف پول برداشت", desc: "موجودی و تراکنش‌های شبکه (BSC)" },
    { to: "/admin/broadcast", emoji: "📣", title: "پیام همگانی", desc: "ارسال با فیلتر مخاطب" },
    { to: "/admin/support", emoji: "📨", title: "پشتیبانی", desc: "پاسخ به پیام‌های کاربران" },
  ];
  // useLocation just to avoid eslint unused warning if needed in future.
  useLocation();
  return (
    <div>
      <h2>پنل مدیریت</h2>
      {cards.map((c) => (
        <Link
          key={c.to}
          to={c.to}
          style={{ textDecoration: "none", color: "inherit" }}
        >
          <div className="card">
            <div className="row" style={{ alignItems: "center" }}>
              <div style={{ fontSize: 24 }}>{c.emoji}</div>
              <div style={{ flex: 1 }}>
                <div className="title">{c.title}</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {c.desc}
                </div>
              </div>
              <div className="muted">›</div>
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}
