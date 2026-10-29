"""🛡️ sniper/geckoterminal.py — Sumber kedua pembeli awal (v16.6).

GeckoTerminal API — gratis, tanpa API key, support network slug
`robinhood`. Dipakai sebagai KORBORASI screen_insiders (Blockscout tetap
primary karena punya riwayat lengkap + pagination; GeckoTerminal hanya
berisi trade pool DEX).

Rate limit terukur (2026-10-25):
  - burst 3 request cepat → HTTP 429, pulih ~35-40 detik
  - free tier ~25-30 req/menit → kita pakai 25 req/menit conservatively
  - max 16 trade per respons (pagination before_timestamp)

Semua kegagalan jaringan return list kosong — JANGAN raise ke pemanggil.
"""
import threading
import time

try:
    from curl_cffi import requests as _requests
    _IMPERSONATE = "chrome120"
except ImportError:
    import requests as _requests
    _IMPERSONATE = None

API_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "robinhood"

# ---- token-bucket rate limiter (module-level, thread-safe) ----
_MAX_PER_MIN = 25          # maks request per menit
_MIN_INTERVAL = 2.5        # jeda minimum antar request (detik)
_rl_lock = threading.Lock()
_rl_window = []            # timestamps request dalam 60s terakhir
_rl_last = 0.0

_backoff_lock = threading.Lock()


def _throttle():
    """Token-bucket: max 25 req/menit + jeda min 2.5s antar request."""
    global _rl_last
    with _rl_lock:
        now = time.monotonic()
        wait = 0.0
        # jeda minimum antar request
        since = now - _rl_last
        if since < _MIN_INTERVAL:
            wait = _MIN_INTERVAL - since
        # sliding window 60 detik
        while _rl_window and now - _rl_window[0] > 60:
            _rl_window.pop(0)
        if len(_rl_window) >= _MAX_PER_MIN:
            wait = max(wait, 60 - (now - _rl_window[0]))
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _rl_window.append(now)
        _rl_last = now


def _session():
    if _IMPERSONATE:
        return _requests.Session(impersonate=_IMPERSONATE)
    s = _requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
    return s


_SESS = _session()


def _get(path: str):  # noqa: ANN201
    """GET dengan rate-limit + retry 429 (backoff 5s → 10s → 20s).
    Return json dict atau None."""
    global _SESS
    waits = (5, 10, 20)
    for attempt in range(3):
        _throttle()
        try:
            r = _SESS.get(f"{API_BASE}{path}", timeout=15)
        except Exception:
            return None
        if r.status_code == 429 and attempt < 2:
            time.sleep(waits[attempt])
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None
    return None


# ---------- normalisasi trade ----------

def _norm_trade(item: dict) -> dict | None:
    """Baris trades GeckoTerminal → dict ternormalisasi, atau None."""
    attrs = item.get("attributes") or {}
    side = (attrs.get("kind") or "").lower()
    if side not in ("buy", "sell"):
        return None
    wallet = ((attrs.get("tx_from_address") or "") or "").lower()
    if not wallet or not wallet.startswith("0x"):
        return None
    # buy  = token diterima (to_token_amount) dibayar pakai quote (from_token_amount)
    # sell = token dilepas (from_token_amount)
    if side == "buy":
        token_amount = attrs.get("to_token_amount") or "0"
        usd = float(attrs.get("volume_in_usd") or 0)
        price = float(attrs.get("price_to_in_usd") or 0)
    else:
        token_amount = attrs.get("from_token_amount") or "0"
        usd = float(attrs.get("volume_in_usd") or 0)
        price = float(attrs.get("price_from_in_usd") or 0)
    ts = attrs.get("block_timestamp") or ""
    # ISO 8601 → epoch (UTC)
    stamp = 0
    try:
        stamp = int(time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone)
    except (ValueError, TypeError, OverflowError):
        stamp = 0
    return {
        "tx_hash": attrs.get("tx_hash") or "",
        "wallet": wallet,
        "side": side,
        "usd": usd,
        "token_amount": float(token_amount or 0),
        "price_usd": price,
        "ts": stamp,
        "block": int(attrs.get("block_number") or 0),
    }


# ---------- API publik ----------

def pools_of(ca: str) -> list:
    """Daftar alamat pool sebuah token. Gagal → []."""
    data = _get(f"/networks/{NETWORK}/tokens/{ca}/pools")
    if not data:
        return []
    out = []
    for p in data.get("data") or []:
        addr = ((p.get("attributes") or {}).get("address") or "").lower()
        if addr.startswith("0x"):
            out.append(addr)
    return out


def trades(pool_addr: str, before_ts=None) -> list:
    """Trade sebuah pool (max 16 per respons — pagination before_timestamp).
    Gagal → []."""
    path = f"/networks/{NETWORK}/pools/{pool_addr}/trades"
    if before_ts:
        path += f"?before_timestamp={int(before_ts)}"
    data = _get(path)
    if not data:
        return []
    out = []
    for t in data.get("data") or []:
        n = _norm_trade(t)
        if n:
            out.append(n)
    return out
