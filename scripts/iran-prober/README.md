# Iran-side prober

Standalone bash script that runs on a small Iran VPS and feeds the
abr-out marketplace's quality/latency metrics. It mirrors the **Test**
button in 3x-ui's Outbound tab: for each active/pending listing it
spawns a temporary `xray-core` process, builds a real **VLESS-TCP
tunnel** to the seller's Iran-side panel using a dedicated probe client
(added to the inbound at listing-creation time), and measures
end-to-end L7 latency by curl-ing `https://www.google.com/generate_204`
through the tunnel.

The probe is fully decoupled from the bot/Docker stack — only `bash`,
`curl`, `jq`, and `xray` are required.

## Install

```bash
# 1. Install dependencies
apt-get update
apt-get install -y bash curl jq unzip ca-certificates

# 2. Install xray-core MANUALLY (no install script — works on
#    network-restricted Iran VPSes where the upstream installer is blocked).
#    Download the latest release zip on a machine that has internet, then
#    scp it to the Iran box. The asset names follow this pattern:
#       Xray-linux-64.zip       (amd64)
#       Xray-linux-arm64-v8a.zip (arm64)
#    Pick the one that matches `uname -m`.
#    Releases page: https://github.com/XTLS/Xray-core/releases
#
#    On the Iran box, after copying the zip:
mkdir -p /opt/xray
unzip -o /path/to/Xray-linux-64.zip -d /opt/xray
chmod +x /opt/xray/xray
ln -sf /opt/xray/xray /usr/local/bin/xray
xray version   # sanity check

# 3. Drop the script + unit
install -m 0755 iran-prober.sh /usr/local/bin/iran-prober.sh
install -m 0644 iran-prober.service /etc/systemd/system/iran-prober.service

# 4. Configure
cat >/etc/abr-out-prober.env <<'EOF'
API_BASE=https://api.example.com
API_INTERNAL_TOKEN=<same-as-bot-side>
PROBE_INTERVAL_SEC=60
PROBE_TIMEOUT_SEC=10
# Optional overrides:
# XRAY_BIN=/usr/local/bin/xray
# XRAY_LOCAL_PORT=10808
# XRAY_BOOT_WAIT_MS=3000
# L7_TEST_URL=https://www.google.com/generate_204
EOF
chmod 600 /etc/abr-out-prober.env

# 5. Run
systemctl daemon-reload
systemctl enable --now iran-prober
journalctl -u iran-prober -f
```

> **Note for Iran-side networks**: the official `Xray-install` script
> fetches assets from `github.com` and runs `systemd` integration that
> often fails behind filters. The manual download above sidesteps both —
> just bring the zip in any way you can (download on a friendly box,
> scp/rsync over, or even drop it via SFTP). The script only needs the
> `xray` binary on `PATH`; nothing else from the upstream installer is
> used.

## How it works

Every `PROBE_INTERVAL_SEC` seconds the script:

1. `GET ${API_BASE}/internal/prober/listings` with the
   `X-Internal-Token` header. Skips any target without a
   `probe_client_uuid` (legacy rows).
2. For each remaining target, writes `/tmp/iran-prober/probe-<id>.json`
   with a SOCKS5 inbound on `127.0.0.1:${XRAY_LOCAL_PORT}` paired with a
   VLESS-TCP outbound (`address=iran_host`, `port=port`,
   `id=probe_client_uuid`, `network=tcp`, `security=none`).
3. Spawns `xray run -c /tmp/...`, waits up to `XRAY_BOOT_WAIT_MS`
   (default 3 s, matching 3x-ui) for the loopback port to accept
   connections, then fires **a single curl invocation that hits the
   test URL twice in a row** so HTTP/1.1 keep-alive lets the second
   request reuse the SOCKS + TCP + TLS connection paid for by the
   first. The `time_total` reported for the warm request is what we
   record — this is exactly what 3x-ui's "lightning" outbound-test
   button does in [`web/service/outbound.go::testConnection`](https://github.com/MHSanaei/3x-ui/blob/main/web/service/outbound.go).
   Without this trick every probe rebuilds the entire tunnel and the
   reported RTT is inflated 4-7x relative to the panel's number.
4. `kill`s xray, builds a JSON sample
   `{listing_id, rtt_ms, ok, sampled_at}` and accumulates them.
5. POSTs the array to `${API_BASE}/internal/prober/samples`.

The lock at `/var/run/iran-prober.lock` ensures only one instance runs
at a time on the host (so `XRAY_LOCAL_PORT` does not collide).

## What the bot does with the samples

- `aggregate_pings_once` (every 5 min) computes
  `avg_ping_ms` from the last 1h of `ok=true` samples and
  `stability_pct` from the last 24h of all samples
  (`ok_count * 100 / total`).
- `listing_quality_gate_once` (every 30s) promotes a `pending`
  listing to `active` on the first `ok=true` sample, or moves it to
  `broken` once `pending_until_at` (5 min after creation) has passed
  without one. Broken listings keep their panel inbound + probe
  client; the seller sees a "connection failed" badge with a
  "retry test" button, and the prober continues to re-test broken
  rows on a slower cadence so they recover automatically once the
  Iran-side tunnel is healthy again.

## Required env

| Variable             | Notes                                              |
|----------------------|----------------------------------------------------|
| `API_BASE`           | Bot API host, no trailing slash                    |
| `API_INTERNAL_TOKEN` | Must match the bot side's `API_INTERNAL_TOKEN`     |

All other variables have sensible defaults — see the top of
`iran-prober.sh`.
