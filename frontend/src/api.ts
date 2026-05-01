// Tiny API client. All requests authenticate via the Telegram Mini App
// initData passed in the Authorization header as "tma <initData>".

function getInitData(): string {
  return window.Telegram?.WebApp?.initData ?? "";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function parseDetail(raw: unknown, fallback: string): string {
  if (!raw) return fallback;
  if (typeof raw === "string") return raw;
  if (Array.isArray(raw)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return (
      raw
        .map((it: any) => (typeof it === "string" ? it : it?.msg ?? ""))
        .filter(Boolean)
        .join("؛ ") || fallback
    );
  }
  if (typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    if (typeof o.msg === "string") return o.msg;
    if (typeof o.detail === "string") return o.detail;
  }
  return fallback;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Authorization", `tma ${getInitData()}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let r: Response;
  try {
    r = await fetch(path, { ...options, headers });
  } catch {
    throw new ApiError(0, "اتصال به سرور برقرار نشد");
  }
  if (!r.ok) {
    let detail: string = r.statusText || "خطا";
    try {
      const body = await r.json();
      detail = parseDetail(body?.detail ?? body, detail);
    } catch {
      /* ignore non-json bodies */
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

export type Me = {
  telegram_id: number;
  username: string | null;
  role: string;
  balance_usd: string; // Decimal serialised as string
  is_admin: boolean;
  is_blocked: boolean;
  bot_username: string | null;
  tunnel_target_host: string | null;
};

export type Transaction = {
  id: number;
  type: string;
  amount: string;
  currency: string;
  ref: string | null;
  note: string | null;
  created_at: string;
};

export type TransactionsPage = {
  items: Transaction[];
  total: number;
  page: number;
  size: number;
};

export type TxFilter = {
  type?: string; // CSV of TxnType
  direction?: "all" | "credit" | "debit";
  from?: string; // ISO
  to?: string; // ISO
  page?: number;
  size?: number;
};

function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export type Listing = {
  id: number;
  // Optional: the marketplace browse endpoint hides seller-identifying
  // fields (title/iran_host/port/seller_username). They are populated only
  // for the seller's own /mine view and the create response.
  title?: string | null;
  iran_host?: string | null;
  port?: number | null;
  price_per_gb_usd: string;
  avg_ping_ms: number | null;
  sales_count: number;
  seller_username: string | null;
  status: string;
  total_gb_sold: number;
  gb_sold_24h: number;
  // Stability % (0-100). Reserved for a future feature; null when not yet
  // computed by the backend.
  stability_pct: number | null;
};

export type Config = {
  id: number;
  listing_id: number;
  listing_title: string;
  name: string;
  panel_client_email: string;
  vless_link: string;
  status: string;
  last_traffic_bytes: number;
  expiry_at: string | null;
  total_gb_limit: number | null;
};

export type BuyConfigInput = {
  listing_id: number;
  name: string;
  expiry_days?: number | null;
  total_gb_limit?: number | null;
};

export type WithdrawalQuote = {
  amount_usd: string;
  fee_usd: string;
  net_usdt: string;
  gas_price_wei: number;
};

export type Withdrawal = {
  id: number;
  user_id: number;
  amount_usd: string;
  fee_usd: string;
  net_usdt: string;
  to_address: string;
  chain: string;
  asset: string;
  status:
    | "pending"
    | "submitting"
    | "submitted"
    | "confirmed"
    | "failed"
    | "refunded";
  source: "manual" | "auto";
  tx_hash: string | null;
  error_msg: string | null;
  created_at: string;
  updated_at: string;
};

export type WithdrawalsPage = {
  items: Withdrawal[];
  total: number;
  page: number;
  size: number;
};

export type AutoWithdrawConfig = {
  enabled: boolean;
  mode: "time" | "threshold";
  interval_hours: number | null;
  threshold_usd: string | null;
  amount_policy: "full" | "fixed";
  fixed_amount_usd: string | null;
  to_address: string;
  next_run_at: string | null;
  last_run_at: string | null;
  last_withdrawal_id: number | null;
};

export type AutoWithdrawInput = {
  enabled: boolean;
  mode: "time" | "threshold";
  interval_hours?: number | null;
  threshold_usd?: string | number | null;
  amount_policy: "full" | "fixed";
  fixed_amount_usd?: string | number | null;
  to_address: string;
};

export const api = {
  me: () => request<Me>("/api/me"),
  listMyTransactions: (f: TxFilter = {}) =>
    request<TransactionsPage>(
      "/api/me/transactions" +
        qs({
          type: f.type,
          direction: f.direction,
          from: f.from,
          to: f.to,
          page: f.page,
          size: f.size,
        }),
    ),
  listListings: () => request<Listing[]>("/api/listings"),
  listMyListings: () => request<Listing[]>("/api/listings/mine"),
  createListing: (body: {
    title: string;
    iran_host: string;
    port: number;
    price_per_gb_usd: number;
  }) =>
    request<Listing>("/api/listings", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listConfigs: () => request<Config[]>("/api/configs"),
  buyConfig: (body: BuyConfigInput) =>
    request<Config>("/api/configs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // ---------- withdrawals ----------
  getWithdrawalQuote: (amount_usd: string | number) =>
    request<WithdrawalQuote>(
      `/api/withdrawals/quote${qs({ amount_usd: String(amount_usd) })}`,
    ),
  createWithdrawal: (body: { amount_usd: string | number; to_address: string }) =>
    request<Withdrawal>("/api/withdrawals", {
      method: "POST",
      body: JSON.stringify({
        amount_usd: String(body.amount_usd),
        to_address: body.to_address,
      }),
    }),
  listWithdrawals: (params: { page?: number; size?: number } = {}) =>
    request<WithdrawalsPage>(
      `/api/withdrawals${qs({ page: params.page, size: params.size })}`,
    ),
  getWithdrawal: (id: number) =>
    request<Withdrawal>(`/api/withdrawals/${id}`),
  getAutoWithdraw: () =>
    request<AutoWithdrawConfig | null>("/api/withdrawals/auto/config"),
  saveAutoWithdraw: (body: AutoWithdrawInput) =>
    request<AutoWithdrawConfig>("/api/withdrawals/auto/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disableAutoWithdraw: () =>
    request<AutoWithdrawConfig | null>("/api/withdrawals/auto/config", {
      method: "DELETE",
    }),
};

/** Telegram deep-link to open the bot's top-up FSM directly. */
export function topupDeepLink(botUsername: string | null | undefined): string | null {
  if (!botUsername) return null;
  return `https://t.me/${botUsername}?start=topup`;
}

// ---------- Admin ----------

export type AdminUser = {
  telegram_id: number;
  username: string | null;
  role: string;
  is_blocked: boolean;
  balance_usd: string;
  configs_count: number;
  listings_count: number;
  created_at: string;
  started_at: string | null;
};

export type AdminUsersPage = {
  items: AdminUser[];
  total: number;
  page: number;
  size: number;
};

export type AdminUserFilter = {
  q?: string;
  blocked?: "all" | "yes" | "no";
  sort?: "created_at" | "balance" | "username" | "telegram_id";
  order?: "asc" | "desc";
  page?: number;
  size?: number;
};

export type Audience = {
  kind: "all" | "buyers" | "sellers" | "date_range";
  from?: string;
  to?: string;
};

export type BroadcastJob = {
  id: number;
  text: string;
  status: "queued" | "running" | "done" | "failed";
  total: number;
  sent: number;
  failed: number;
  created_at: string;
  finished_at: string | null;
};

export type SupportEntry = {
  id: number;
  user_id: number;
  username: string | null;
  direction: "in" | "out";
  text: string;
  replied_by_admin_id: number | null;
  created_at: string;
};

export const adminApi = {
  listUsers: (f: AdminUserFilter = {}) =>
    request<AdminUsersPage>(
      "/api/admin/users" +
        qs({
          q: f.q,
          blocked: f.blocked,
          sort: f.sort,
          order: f.order,
          page: f.page,
          size: f.size,
        }),
    ),
  getUser: (id: number) => request<AdminUser>(`/api/admin/users/${id}`),
  setBlocked: (id: number, blocked: boolean) =>
    request<AdminUser>(`/api/admin/users/${id}/block`, {
      method: "POST",
      body: JSON.stringify({ blocked }),
    }),
  addTransaction: (
    id: number,
    body: { amount: string | number; type: string; note: string },
  ) =>
    request<Transaction>(`/api/admin/users/${id}/transactions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listUserTransactions: (id: number, f: TxFilter = {}) =>
    request<TransactionsPage>(
      `/api/admin/users/${id}/transactions` +
        qs({
          type: f.type,
          direction: f.direction,
          from: f.from,
          to: f.to,
          page: f.page,
          size: f.size,
        }),
    ),
  sendDM: (id: number, text: string) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}/message`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  broadcastPreview: (audience: Audience) =>
    request<{ count: number }>(`/api/admin/broadcast/preview`, {
      method: "POST",
      body: JSON.stringify({ audience }),
    }),
  broadcast: (text: string, audience: Audience) =>
    request<BroadcastJob>(`/api/admin/broadcast`, {
      method: "POST",
      body: JSON.stringify({ text, audience }),
    }),
  getBroadcast: (id: number) => request<BroadcastJob>(`/api/admin/broadcast/${id}`),
  listSupport: (params: { only_unanswered?: boolean; page?: number; size?: number } = {}) =>
    request<{ items: SupportEntry[]; total: number; page: number; size: number }>(
      `/api/admin/support` +
        qs({
          only_unanswered: params.only_unanswered ? "1" : undefined,
          page: params.page,
          size: params.size,
        }),
    ),
  replySupport: (id: number, text: string) =>
    request<{ ok: boolean }>(`/api/admin/support/${id}/reply`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
};
