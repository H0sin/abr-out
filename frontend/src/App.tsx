import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  CartIcon,
  SignalIcon,
  StoreIcon,
  WalletIcon,
} from "./components/icons";
import { ThemeToggle } from "./components/ThemeToggle";
import { useMe } from "./lib/MeContext";
import { haptic } from "./lib/useTelegram";

export function App() {
  const loc = useLocation();
  const { me } = useMe();
  const isAdmin = Boolean(me?.is_admin);
  return (
    <div className="app">
      <ThemeToggle />
      <div className="content" key={loc.pathname}>
        <Outlet />
      </div>
      <nav className={"tabbar" + (isAdmin ? " tabbar-5" : "")}>
        <Tab to="/browse" label="خرید" icon={<CartIcon />} />
        <Tab to="/my" label="کانفیگ‌ها" icon={<SignalIcon />} />
        <Tab to="/sell" label="فروش" icon={<StoreIcon />} />
        <Tab to="/wallet" label="کیف پول" icon={<WalletIcon />} />
        {isAdmin && (
          <Tab to="/admin" label="ادمین" icon={<span style={{ fontSize: 18 }}>🛠</span>} />
        )}
      </nav>
    </div>
  );
}

function Tab({
  to,
  icon,
  label,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <NavLink
      to={to}
      onClick={() => haptic.selection()}
      className={({ isActive }) => "tab" + (isActive ? " active" : "")}
    >
      {icon}
      <span>{label}</span>
    </NavLink>
  );
}
