// Stroke icons for the tabbar. Inherit `currentColor` so active tab can
// recolor via CSS. 24x24 viewBox, 2px stroke.

type Props = { className?: string };

const base = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function CartIcon(p: Props) {
  return (
    <svg {...base} className={p.className}>
      <path d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6H18a2 2 0 0 0 2-1.6L21 8H6" />
      <circle cx="9" cy="20" r="1.5" />
      <circle cx="17" cy="20" r="1.5" />
    </svg>
  );
}

export function SignalIcon(p: Props) {
  return (
    <svg {...base} className={p.className}>
      <path d="M4 18v-2" />
      <path d="M9 18v-5" />
      <path d="M14 18V9" />
      <path d="M19 18V5" />
    </svg>
  );
}

export function StoreIcon(p: Props) {
  return (
    <svg {...base} className={p.className}>
      <path d="M3 7l1.5-3h15L21 7" />
      <path d="M3 7v2a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0V7" />
      <path d="M5 11v9h14v-9" />
      <path d="M10 20v-5h4v5" />
    </svg>
  );
}

export function WalletIcon(p: Props) {
  return (
    <svg {...base} className={p.className}>
      <path d="M3 7a2 2 0 0 1 2-2h13l3 3v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
      <path d="M16 13h3" />
      <circle cx="17" cy="13" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function CopyIcon(p: Props) {
  return (
    <svg {...base} className={p.className} width={16} height={16}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

export function RefreshIcon(p: Props) {
  return (
    <svg {...base} className={p.className} width={18} height={18}>
      <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.3L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}
