import { NavLink, Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="app">
      <div className="content">
        <Outlet />
      </div>
      <nav className="tabbar">
        <Tab to="/browse" icon="🛒" label="خرید" />
        <Tab to="/my" icon="📡" label="کانفیگ‌ها" />
        <Tab to="/sell" icon="💰" label="فروش" />
        <Tab to="/wallet" icon="👛" label="کیف پول" />
      </nav>
    </div>
  );
}

function Tab({ to, icon, label }: { to: string; icon: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => "tab" + (isActive ? " active" : "")}
    >
      <span className="icon">{icon}</span>
      <span>{label}</span>
    </NavLink>
  );
}
