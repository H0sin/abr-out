// Tiny API client. All requests authenticate via the Telegram Mini App
// initData passed in the Authorization header as "tma <initData>".

const initData = window.Telegram?.WebApp?.initData ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Authorization", `tma ${initData}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const r = await fetch(path, { ...options, headers });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
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
};

export type Listing = {
  id: number;
  title: string;
  iran_host: string;
  port: number;
  price_per_gb_usd: string;
  avg_ping_ms: number | null;
  sales_count: number;
  seller_username: string | null;
  status: string;
};

export type Config = {
  id: number;
  listing_id: number;
  listing_title: string;
  panel_client_email: string;
  vless_link: string;
  status: string;
  last_traffic_bytes: number;
};


export const api = {
  me: () => request<Me>("/api/me"),
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
  buyConfig: (listing_id: number) =>
    request<Config>("/api/configs", {
      method: "POST",
      body: JSON.stringify({ listing_id }),
    }),
};
