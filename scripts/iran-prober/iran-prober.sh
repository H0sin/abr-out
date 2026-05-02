#!/usr/bin/env bash
# Iran-side outbound prober for the abr-out marketplace.
#
# Mirrors the "Test" button in 3x-ui's Outbound tab:
#
#   1. GET /internal/prober/listings (auth: X-Internal-Token)
#   2. For every target with a probe_client_uuid, write a temp xray config
#      that pairs a local SOCKS5 inbound with a real VLESS-TCP outbound to
#      iran_host:port (using the dedicated probe client).
#   3. Spawn xray-core, warm up the connection with one HTTPS GET, then
#      measure a second GET to www.google.com/generate_204 through the
#      tunnel — that "time_total" is the end-to-end L7 latency the buyer
#      would experience.
#   4. POST samples back to /internal/prober/samples.
#
# This script is intentionally standalone: it has no Python deps, no
# Docker compose, and is independent of the bot codebase. Drop it on a
# small Iran-side VPS, install xray + curl + jq, point API_BASE at the
# bot's API host and run as a systemd service (see iran-prober.service).
#
# Required env:
#   API_BASE              e.g. https://api.example.com (no trailing slash)
#   API_INTERNAL_TOKEN    matches API_INTERNAL_TOKEN on the bot side
#
# Optional env (with defaults):
#   PROBE_INTERVAL_SEC    60     seconds between cycles
#   PROBE_TIMEOUT_SEC     10     per-URL curl timeout (matches 3x-ui's 10s)
#   XRAY_BIN              xray   path to the xray-core binary
#   XRAY_LOCAL_PORT       10808  loopback SOCKS port reused across probes
#   XRAY_BOOT_WAIT_MS     3000   wait for xray to start listening (3x-ui uses 3s)
#   L7_TEST_URL           https://www.google.com/generate_204
#   API_INSECURE          0      set 1 to add `curl -k` (self-signed cert,
#                                e.g. when API_BASE is a raw IP behind
#                                Caddy's `tls internal`)
#
# Exit codes:
#   0  loop exited cleanly (only on SIGTERM)
#   1  missing required env or dependency

set -u
set -o pipefail

readonly LOCK_FILE="${LOCK_FILE:-/var/run/iran-prober.lock}"
readonly TMP_DIR="${TMP_DIR:-/tmp/iran-prober}"

API_BASE="${API_BASE:-}"
API_INTERNAL_TOKEN="${API_INTERNAL_TOKEN:-}"
PROBE_INTERVAL_SEC="${PROBE_INTERVAL_SEC:-60}"
PROBE_TIMEOUT_SEC="${PROBE_TIMEOUT_SEC:-10}"
XRAY_BIN="${XRAY_BIN:-xray}"
XRAY_LOCAL_PORT="${XRAY_LOCAL_PORT:-10808}"
XRAY_BOOT_WAIT_MS="${XRAY_BOOT_WAIT_MS:-3000}"L7_TEST_URL="${L7_TEST_URL:-https://www.google.com/generate_204}"
# Set API_INSECURE=1 if API_BASE points at a self-signed endpoint
# (e.g. raw IP behind Caddy's `tls internal`). Adds curl -k.
API_INSECURE="${API_INSECURE:-0}"
API_CURL_FLAGS=()
if [[ "$API_INSECURE" == "1" ]]; then
    API_CURL_FLAGS+=(-k)
fi

log() {
    printf '%s [iran-prober] %s\n' "$(date -u +%FT%TZ)" "$*" >&2
}

die() {
    log "FATAL: $*"
    exit 1
}

require_env() {
    local name="$1"
    if [[ -z "${!name}" ]]; then
        die "missing required env: $name"
    fi
}

require_bin() {
    local bin="$1"
    if ! command -v "$bin" >/dev/null 2>&1; then
        die "missing required binary in PATH: $bin"
    fi
}

# --- one-time validation ---------------------------------------------------

require_env API_BASE
require_env API_INTERNAL_TOKEN
require_bin curl
require_bin jq
if ! command -v "$XRAY_BIN" >/dev/null 2>&1; then
    die "xray binary not found: XRAY_BIN=$XRAY_BIN"
fi

mkdir -p "$TMP_DIR"

# --- xray config generator -------------------------------------------------
#
# We write JSON via jq so quoting/escaping is bulletproof even when the
# probe email or remark contains odd characters.
write_xray_config() {
    local cfg_path="$1"
    local server_host="$2"
    local server_port="$3"
    local client_uuid="$4"

    jq -n \
        --arg host "$server_host" \
        --argjson port "$server_port" \
        --arg uuid "$client_uuid" \
        --argjson local_port "$XRAY_LOCAL_PORT" \
        '{
            log: { loglevel: "warning", access: "none", error: "none" },
            inbounds: [{
                tag: "probe-in",
                listen: "127.0.0.1",
                port: $local_port,
                protocol: "socks",
                settings: { auth: "noauth", udp: true }
            }],
            outbounds: [{
                tag: "probe-out",
                protocol: "vless",
                settings: {
                    vnext: [{
                        address: $host,
                        port: $port,
                        users: [{
                            id: $uuid,
                            encryption: "none",
                            flow: ""
                        }]
                    }]
                },
                streamSettings: {
                    network: "tcp",
                    security: "none",
                    tcpSettings: { header: { type: "none" } }
                }
            }, {
                tag: "block",
                protocol: "blackhole"
            }],
            routing: {
                domainStrategy: "AsIs",
                rules: [{
                    type: "field",
                    inboundTag: ["probe-in"],
                    network: "tcp,udp",
                    outboundTag: "probe-out"
                }]
            }
        }' \
        > "$cfg_path"
}

# --- single probe ----------------------------------------------------------
#
# Echoes a single JSON object suitable for /internal/prober/samples:
#   { listing_id, rtt_ms (or null), ok, sampled_at }
probe_one() {
    local listing_id="$1"
    local server_host="$2"
    local server_port="$3"
    local client_uuid="$4"

    local now_iso
    now_iso="$(date -u +%FT%TZ)"
    local cfg_path="$TMP_DIR/probe-$listing_id.json"
    local pid="" rtt_ms="null" ok="false" curl_out=""

    write_xray_config "$cfg_path" "$server_host" "$server_port" "$client_uuid"

    "$XRAY_BIN" run -c "$cfg_path" >/dev/null 2>&1 &
    pid=$!

    # Tear down xray on every return path.
    cleanup() {
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
        rm -f "$cfg_path"
    }
    trap cleanup RETURN

    # Wait for the loopback SOCKS port to start accepting connections
    # (and for xray to have wired up the outbound). 3x-ui waits up to
    # 3 seconds before giving up.
    local waited=0
    while ! curl -s --connect-timeout 1 -o /dev/null \
        --socks5-hostname "127.0.0.1:$XRAY_LOCAL_PORT" \
        --max-time 1 "https://www.google.com/generate_204" >/dev/null 2>&1; do
        waited=$((waited + 100))
        if [[ "$waited" -ge "$XRAY_BOOT_WAIT_MS" ]]; then
            break
        fi
        sleep 0.1
    done

    # Latency measurement, mirroring 3x-ui's TestOutbound exactly:
    # one curl invocation hits the test URL TWICE so HTTP/1.1
    # keep-alive lets the second request reuse the same SOCKS + TCP +
    # TLS connection that was paid for by the first. -w prints once
    # per URL; we keep only the last line (the warm request) and
    # discard the first. Without this trick every request rebuilds
    # the entire tunnel, inflating the reported RTT 4-7x relative to
    # what the 3x-ui "lightning" button shows.
    curl_out="$(curl -sS --http1.1 -o /dev/null -o /dev/null \
        --socks5-hostname "127.0.0.1:$XRAY_LOCAL_PORT" \
        --max-time "$PROBE_TIMEOUT_SEC" \
        --connect-timeout 5 \
        --keepalive-time 30 \
        -w '%{http_code} %{time_total}\n' \
        "$L7_TEST_URL" "$L7_TEST_URL" 2>/dev/null \
        | tail -n 1 || true)"

    local code="" time_total=""
    read -r code time_total <<<"$curl_out"

    if [[ "$code" == "204" || "$code" == "200" ]]; then
        # bash arithmetic doesn't do floats; use awk to round.
        rtt_ms="$(awk -v t="$time_total" 'BEGIN { printf "%d", t * 1000 + 0.5 }')"
        ok="true"
    fi

    cleanup
    trap - RETURN

    jq -n \
        --argjson listing_id "$listing_id" \
        --argjson rtt_ms "$rtt_ms" \
        --argjson ok "$ok" \
        --arg sampled_at "$now_iso" \
        '{
            listing_id: $listing_id,
            rtt_ms: $rtt_ms,
            ok: $ok,
            sampled_at: $sampled_at
        }'
}

# --- one cycle -------------------------------------------------------------
cycle() {
    local listings_json
    listings_json="$(curl -s --max-time 15 "${API_CURL_FLAGS[@]}" \
        -H "X-Internal-Token: $API_INTERNAL_TOKEN" \
        "$API_BASE/internal/prober/listings" || true)"

    if [[ -z "$listings_json" ]] || ! jq -e 'type == "array"' \
        <<<"$listings_json" >/dev/null 2>&1; then
        log "fetch listings failed; raw: ${listings_json:0:200}"
        return
    fi

    local count
    count="$(jq 'length' <<<"$listings_json")"
    log "probing $count target(s)"

    local samples='[]'
    local i=0
    while [[ "$i" -lt "$count" ]]; do
        local row uuid host port lid
        row="$(jq -c ".[$i]" <<<"$listings_json")"
        uuid="$(jq -r '.probe_client_uuid // empty' <<<"$row")"
        if [[ -z "$uuid" ]]; then
            log "skip target index=$i (no probe_client_uuid)"
            i=$((i + 1))
            continue
        fi
        host="$(jq -r '.iran_host' <<<"$row")"
        port="$(jq -r '.port' <<<"$row")"
        lid="$(jq -r '.listing_id' <<<"$row")"

        local sample
        sample="$(probe_one "$lid" "$host" "$port" "$uuid")"
        samples="$(jq -c ". + [$sample]" <<<"$samples")"
        i=$((i + 1))
    done

    local n
    n="$(jq 'length' <<<"$samples")"
    if [[ "$n" -gt 0 ]]; then
        local resp
        resp="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${API_CURL_FLAGS[@]}" \
            -X POST \
            -H "X-Internal-Token: $API_INTERNAL_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$samples" \
            "$API_BASE/internal/prober/samples" || true)"
        log "posted $n sample(s) -> http $resp"
    fi
}

# --- main loop -------------------------------------------------------------
log "starting; api_base=$API_BASE interval=${PROBE_INTERVAL_SEC}s timeout=${PROBE_TIMEOUT_SEC}s"

stopping=0
trap 'stopping=1' TERM INT

# flock prevents two instances from racing on the same XRAY_LOCAL_PORT.
exec 9>"$LOCK_FILE" || die "cannot open lock file $LOCK_FILE"
flock -n 9 || die "another iran-prober is already running ($LOCK_FILE)"

while [[ "$stopping" -eq 0 ]]; do
    cycle || log "cycle errored; continuing"
    # Sleep in 1s slices so SIGTERM is responsive.
    s=0
    while [[ "$s" -lt "$PROBE_INTERVAL_SEC" && "$stopping" -eq 0 ]]; do
        sleep 1
        s=$((s + 1))
    done
done

log "stopped"
