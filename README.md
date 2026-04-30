# abr-out

مارکت‌پلیس اوتباند V2Ray در بستر تلگرام. فروشنده‌ها از پنل 3x-ui ایرانشان به سرور خارج ما تونل می‌زنند، خریدارها با کیف پول دلاری به ازای **مصرف واقعی** هزینه پرداخت می‌کنند.

> این یک شالوده MVP است. جزئیات پلن کامل در حافظه‌ی نشست (`/memories/session/plan.md`).

## Architecture

```
[Buyer] ──VLESS TCP──▶ [Seller-Iran:port] ──tunnel(3x-ui ایران)──▶ [Foreign 3x-ui:port]
[Bot] ◀─▶ [API] ◀─▶ [Postgres] [Redis]   ───3x-ui HTTP API──┘
                  ▲
            [Iran Prober (xray-core)]
```

Services (docker-compose):
- `postgres`, `redis`
- `api` — FastAPI (internal endpoints + payment webhooks)
- `bot` — aiogram 3 (Telegram UI)
- `worker` — APScheduler (poll traffic, billing, balance enforcement, ping aggregation)
- `prober` — جدا، روی سرور ایران، با docker-compose.prober.yml

## Local development

```powershell
copy .env.example .env
# مقادیر BOT_TOKEN, XUI_*, NOWPAYMENTS_* را پر کن
docker compose up -d --build
docker compose run --rm api alembic revision --autogenerate -m "init"
docker compose run --rm api alembic upgrade head
```

> اولین بار باید migration اولیه را با autogenerate بسازی، سپس commit کنی.

## Deploy (foreign server)

1. روی سرور خارج: docker، docker compose، 3x-ui (جدا روی همان host).
2. کلون مخزن، کپی `.env.example` به `.env` و پر کردن مقادیر.
3. در `docker-compose.prod.yml` مقدار `IMAGE` را به `ghcr.io/<your-user>/abr-out:latest` تغییر بده (یا متغیر `IMAGE` را در محیط ست کن).
4. اولین deploy:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose run --rm api alembic upgrade head
```

5. هر push روی `main` در GitHub، ایمیج جدید را روی GHCR می‌گذارد. روی سرور:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Iran prober

روی سرور ایران (هر کجا که latency تا کاربر ایرانی واقع‌گرایانه است):

```bash
git clone <repo>
cd <repo>
cp .env.example .env  # فقط API_BASE و API_INTERNAL_TOKEN لازم است
docker compose -f docker-compose.prober.yml up -d --build
```

## Bot menu

- 🛒 خرید
- 💰 فروش
- 👛 کیف پول

(جریان‌های کامل خرید/فروش در فاز بعد پیاده می‌شوند.)

## Billing model

- ارز پایه: **USD**
- قیمت‌گذاری per-GB توسط فروشنده.
- خریدار مبلغی شارژ می‌کند، **زیر همه** اینباندهای فعال کانفیگ نامحدود می‌گیرد.
- worker هر `TRAFFIC_POLL_INTERVAL_SEC` ثانیه از 3x-ui مصرف را می‌خواند:
  - فروشنده: `gb × price_per_gb_usd`
  - خریدار: `gb × price_per_gb_usd × (1 + COMMISSION_PCT)`
  - کارمزد: تفاضل به ادمین.
- اگر موجودی خریدار به صفر رسید همه کانفیگ‌هایش disable می‌شوند تا شارژ کند.

## Env variables

به [.env.example](.env.example) رجوع کن.

| نام | پیش‌فرض | شرح |
| --- | --- | --- |
| `COMMISSION_PCT` | `0.15` | درصد کارمزد روی هر GB |
| `MIN_TOPUP_USD` | `2` | حداقل اولین شارژ برای فعال‌سازی کانفیگ‌ها |
| `TRAFFIC_POLL_INTERVAL_SEC` | `60` | فاصله سیکل بیلینگ |
