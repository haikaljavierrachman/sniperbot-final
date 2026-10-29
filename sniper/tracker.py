"""🕵️ INSIDER TRACKER (v16.1/v16.8) — intel insider on-chain, ALERT-ONLY.

Sumber sinyal KETIGA HoodSniper: memantau transaksi blok baru di Robinhood
Chain (chainId 4663), mengecek apakah pengirimnya wallet insider (hasil
screening tools/screen_insiders.py, tersimpan di tabel insider_wallets),
lalu kalau insider itu memanggil SwapRouter02 untuk BUY token →
Kirim NOTIF TELEGRAM RINGKAS (wallet, simbol+CA, mcap/liq, chart) seperti
intel pasar — TANPA masuk pipa listener.

Mode setting `insider_alert_only`:
  "1" (DEFAULT) = ALERT-ONLY — tidak ada posisi baru, tidak ada evaluasi
      buy, tidak print listener. Notif via listener._notify / bind_notify.
  "0" = perilaku LAMA — CA diteruskan ke pipa bot yang sama
      (listener.process_ca, allow_buy=True).

Desain:
- Async loop INDEPENDEN (task background bot.py), on/off via setting tracker_on.
- Fingerprint calldata Uniswap: selector 4 byte pertama.
    exactInputSingle = 0x414bf389  (input pertama = chunk abi-encoded
      tokenIn(32) | tokenOut(32) | fee(32) | recipient(32) | ...)
    exactInput       = 0xc04b8d59  (path bytes multicall — diekstrak regex)
    multicall(bytes[]) = 0xac9650d8 — byte-data tiap sub-call dianalisis rekursif
- ROUTE_SELECTOR fallback: kalau `to` adalah router tapi selector tak dikenali,
  tokenOut dicari dari alamat yang bukan WETH/USDG sentinel di raw data.
- Anti-melewatkan blok: packet per geth 'newHeads' ~2 dtk → probe kumpulan blok
  dari latest-1 (fingerprint finger_blok terakhir) sampai beberapa dtk penuh
  (BLOCK_GRACE). Finger bertahan lintas restart agar blok tidak dipindai ulang.
- Aman diganggu RPC gagal: try/except + sleep; loop tidak mati.
- Deadlock-free: Router py 直 punya executor sendiri, AMBIL KONTRAK SEKALI
  SAAT SETUP (di-clone per-loop agar pytest bisa patch config.RPC_URL).
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import threading

from . import config, db, listener
from . import dexscreener as dx

# ============================ KONSTANTA ============================

SWAP_ROUTER02 = "0xCaf681a66D020601342297493863E78C959E5cb2".lower()

# Selector 4-byte pertama dari calldata (fungsi Uniswap SwapRouter02)
EXACT_INPUT_SINGLE = "0x414bf389"   # exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))
EXACT_INPUT        = "0xc04b8d59"   # exactInput(bytes) — path ter-encode
MULTICALL          = "0xac9650d8"   # multicall(bytes[])

# Quote assets Robinhood Chain — dipakai buat membedakan tokenIn vs tokenOut
WETH  = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73".lower()
USDG  = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168".lower()
NATIVE_SENTINEL = "0x0000000000000000000000000000000000000000"
QUOTE_ASSETS = frozenset({WETH, USDG, NATIVE_SENTINEL})

# Buat proteksi slippage: bandingkan harga pasar Dexscreener sebelum vs
# sesudah stagger INSIDER processing — jika harga lari > MAX_SLIP_PCT%
# dalam selang ini, kemungkinan insider buy di puncak momentum.
MAX_SLIP_PCT = 5.0

# Kalau insider membeli lalu harga langsung anjlok, jangan kejar.
# (sudah tertangani listener.process_ca staleness gate + safety filter)

BLOCK_GRACE = 6.0   # dtk kedaluwarsa menanti blok "hampir" terlewat
POLL_IDLE = 0.5     # dtk kalau tidak ada blok baru (Robinhood Chain ~10 blok/dtk)

# ============ BATCH LOG SCAN (anti-lag) ============
# Node publik Robinhood Chain menolak WSS (Cloudflare/Envoy HTTP 400) dan
# eth_get_block per-blok lambat (~270ms) → tertinggal 7 blok/dtk. Solusi:
# eth_getLogs batch — 50–300 blok dalam 1 RPC call (~250ms). Filter topic
# ERC20 Transfer(address indexed from, address indexed to) dengan to =
# wallet insider. Event = insider menerima/membeli token asli (BUKAN quote).
TRANSFER_TOPIC = ("0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55"
                  "a4df523b3ef")
BATCH_MAX_BLOCKS = 100   # sapu maks 100 blok per siklus batch
CATCHUP_LIMIT = 500      # kalau tertinggal > 500 blok → fast-forward
CATCHUP_KEEP = 50        # ...tapi tetap pindai 50 blok terakhir
POLL_INTERVAL = 2.5      # 2.5s ≈ ~25 blok baru di Robinhood Chain

# Pola deteksi token dalam path bytes (exactInput/multicall path):
# data dexscreener adalah HEX TELANJANG (tanpa "0x"), jadi regex cari
# run hex ≥40 karakter lalu ambil 40 karakter pertama dari tiap run
# (alamat EVM 20B selalu muncul sebagai 40 hex di kolom kiri word).

# Dexscreener / Blockscout base URLs (dipakai tersier di screen_insiders, di sini
# hanya untuk _price_grace pasca-arah track agar tidak double-fetch)


# ============================ UTIL ============================

def _token_out_from_exact_input_single(data_hex: str):
    """Layout calldata (hex chars):
       [0:8]  selector
       [8:72] offset params (= 0x20)
       [72:136] tokenIn  (alamat = 40 hex terakhir dari word)
       [136:200] tokenOut
    """
    data = data_hex.lower().replace("0x", "")
    if len(data) < 200:
        return None
    tok_in = data[96:136]     # 40 hex terakhir word-1
    tok_out = data[160:200]   # alamat dari word-2
    if len(tok_out) != 40 or int(data[8:72] or "0", 16) != 0x20:
        # offset bukan 0x20 (layout aneh) → fallback regex universal
        return None
    if tok_out in {"0" * 40} or ("0x" + tok_out) in QUOTE_ASSETS:
        # JUAL: tokenOut = quote → token sebenarnya adalah tokenIn
        return ("0x" + tok_in) if tok_in not in {"0" * 40} else None
    return "0x" + tok_out


def _words(data_hex: str):
    """Pecah calldata jadi ABI words 64-hex. Layout: [0:8] selector,
    lalu tiap word-ke-i = data[8 + i*64 : 8 + (i+1)*64]."""
    data = data_hex.lower().replace("0x", "")
    out = [data[:8]]     # word-0 = selector 4-byte
    i = 8
    while i + 64 <= len(data):
        out.append(data[i:i + 64])
        i += 64
    return out


def _tokens_from_path(data_hex: str) -> list:
    """Tarik SEMUA kandidat alamat EVM dari calldata ABI-encoded.

    Alamat 20B dalam ABI selalu tampak sebagai 40-hex di sisi KANAN
    sebuah word 64-hex dengan 24-hex nol di kiri (left-pad 12 byte).
    Word yang tidak memenuhi pola ini (fee, amount, offset) REJECT.
    """
    out, seen = [], set()
    for word in _words(data_hex):
        if len(word) != 64:
            continue
        head, tail = word[:24], word[24:]
        if head != "0" * 24 or tail == "0" * 40 or int(tail, 16) <= 0xffff:
            continue
        if not all(c in "0123456789abcdef" for c in tail):
            continue
        cand = "0x" + tail
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def extract_token_out(data_hex: str):
    """Ekstrak address token TARGET (tokenOut) dari calldata router.
       Return: alamat 20B 0x-hex huruf kecil, atau None kalau gagal.

       Mendukung:
         - exactInputSingle: tokenOut langsung dari word kedua params
         - exactInput / multicall: semua alamat di path dicicipi, dipilih
           satu yang BUKAN quote asset (WETH/USDG/sentinel).
    """
    if not data_hex or len(data_hex) < 74:
        return None
    d = data_hex.lower()
    sel = d[:10]
    if sel == EXACT_INPUT_SINGLE:
        tok = _token_out_from_exact_input_single(d)
        if tok and tok not in QUOTE_ASSETS:
            return tok
        # kalau tok None/quote → jatuh ke fallback universal di bawah
    if sel not in (EXACT_INPUT_SINGLE, EXACT_INPUT, MULTICALL):
        return None   # selector tidak dikenal → jangan menebak-nebak
    # fallback universal: semua alamat 20B di data, kecualikan quote
    cands = [t for t in _tokens_from_path(d)
             if t.lower() not in QUOTE_ASSETS]
    return cands[-1].lower() if cands else None


def is_buy_call(data_hex: str, from_token=None) -> bool:
    """Deteksi apakah transaksi router ini mengarah ke BUY token
       (bukan SELL). Simpel: tokenIn harus quote asset (WETH/USDG).
       Heuristik 'semua alamat di data: tokenIn = alamat PERTAMA'. """
    toks = [t.lower() for t in _tokens_from_path(data_hex)]
    if not toks:
        return False
    tok_in = toks[0]
    return tok_in in QUOTE_ASSETS


# ============================ PIPA ============================

def _src() -> dict:
    """Sumber sinyal untuk insider tracker — memakai sumber sinyal scanner
       kalau ada; kalau tidak, buat source sintetis src dict."""
    for s in db.sources(only_enabled=True):
        if (s.get("name") or "").startswith("Scanner"):
            return s
    return {"id": None, "chat_id": 0, "topic_id": 0,
            "name": "INSIDER_TRACKER", "mcap_max": 50000}


def _short_wallet(addr: str, labels: dict = None) -> str:
    """Label singkat wallet: label DB bila ada, kalau tidak 0xabc…def."""
    a = (addr or "").lower()
    if labels and a in labels:
        return labels[a]
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 10 else (a or "?")


def _insider_labels(addrs) -> dict:
    """Map address → label DB (insider_wallets), untuk notif alert."""
    labels = {}
    try:
        for row in db.list_insiders():
            addr = (row.get("address") or "").lower()
            label = (row.get("label") or "").strip()
            if addr and label and addr in addrs:
                labels[addr] = label
    except Exception:
        pass
    return labels


def _fmt_usd_compact(v) -> str:
    """$812 / $3.1k / $1.2M — '?' bila tak tersedia."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1000:
        s = f"${v / 1000:.1f}k"
        return s.replace(".0k", "k")
    return f"${v:,.0f}"


def _insider_alert_text(pair, ca, wallet_label: str,
                        extra_buys: int = 0, dedup_window_sec: int = 0) -> str:
    """Teks notif alert insider — maks 6 baris, tanpa emoji jualan.

    extra_buys > 0 → baris wallet diberi penghitung dedup:
    "(+N beli lagi dalam 10m)" — notif mini gabungan, bukan alert penuh.
    """
    sym = (pair.get("baseToken") or {}).get("symbol", "?") if pair else "?"
    mcap = dx.mcap_of(pair) if pair else 0
    liq = dx.liq_usd_or_none(pair) if pair else None
    pair_addr = (pair or {}).get("pairAddress", "")
    chain = (pair or {}).get("chainId") or "robinhood"
    chart = f"https://dexscreener.com/{chain}/{pair_addr}" if pair_addr else ""
    wallet_line = f"wallet `{wallet_label}`"
    if extra_buys > 0:
        window_txt = "?"
        if dedup_window_sec:
            if dedup_window_sec % 3600 == 0:
                window_txt = f"{dedup_window_sec // 3600}h"
            elif dedup_window_sec % 60 == 0:
                window_txt = f"{dedup_window_sec // 60}m"
            else:
                window_txt = f"{dedup_window_sec}s"
        wallet_line = f"wallet `{wallet_label}` (+{extra_buys} beli lagi dalam {window_txt})"
    lines = [
        "🕵️ **INSIDER BUY — {}**".format(sym),
        wallet_line,
        f"`{ca}`",
        f"📊 mcap {_fmt_usd_compact(mcap)} · liq {_fmt_usd_compact(liq) if liq is not None else 'n/a'}",
    ]
    if chart:
        lines.append(f"📈 {chart}")
    return "\n".join(lines)


def _deliver_alert(text: str):
    """Kirim alert via listener._notify bila ada; fallback → callback global
    tracker (bind_notify dari bot.py). Detached task — tracker loop tidak
    menunggu notif selesai. Tidak boleh raise."""
    cb = getattr(listener, "_notify", None) or _notify_cb
    if cb is None:
        print(f"[insider] (alert tak terkirim — notify belum terikat)\n{text}")
        return
    try:
        asyncio.create_task(cb(text))
    except Exception as e:
        print(f"[insider] notify fail: {e}")


_INSIDER_RETRY_DELAY = 30.0  # detik — retry best_pair sebelum alert


# ==================== DEDUP ALERT (wallet, CA) ====================
# State process-level: (wallet_lower, ca_lower) -> (last_alert_ts, extra_count).
# Dedup dihitung hanya untuk alert yang BENAR-BENAR dikirim ke Telegram
# (bukan yang dibiyangkan best_pair None).(dict plain aman: tracker jalan
# satu loop asyncio; lock hanya untuk task background/retry.)
_alert_dedup: dict = {}
_alert_dedup_lock = threading.Lock()


def _dedup_window_sec() -> int:
    """Jendela dedup (detik) dari setting db. 0/negatif/berisik → 0 (mati)."""
    try:
        v = int(db.get("insider_alert_dedup_window_sec", str) or "600")
    except Exception:
        v = 600
    return v if v > 0 else 0


def _dedup_check_send(wallet: str, ca: str, pair, now=None):
    """Dedup per-CA koin (1 koin = maks 1 notif chat per jendela) + catat ke DB
    insider_buys agar frekuensi beli tiap koin bisa dipantau lewat tombol Log Beli.
    """
    try:
        win = _dedup_window_sec()
        ts = (now if now is not None else time.time())
        try:
            sym = ((pair or {}).get("baseToken") or {}).get("symbol") or "?"
            db.record_insider_buy(
                ca, wallet, symbol=sym,
                mcap=dx.mcap_of(pair) if pair else 0,
                liq=dx.liq_usd_or_none(pair) or 0 if pair else 0,
                pair_address=(pair or {}).get("pairAddress") or "",
                ts=int(ts))
        except Exception:
            pass
        key = (ca or "").lower()
        extra = 0
        with _alert_dedup_lock:
            state = _alert_dedup.get(key)
            if win and state and ts - state[0] < win:
                _alert_dedup[key] = (state[0], state[1] + 1)
                return None
            if state:
                extra = state[1]
            if len(_alert_dedup) > 500:  # ponytail: prune murah, tanpa thread
                for k in [k for k, v in _alert_dedup.items()
                          if ts - v[0] >= max(win, 1)]:
                    del _alert_dedup[k]
            _alert_dedup[key] = (ts, 0)
        labels = _insider_labels({wallet.lower()} if wallet else set())
        return _insider_alert_text(
            pair, ca, _short_wallet(wallet, labels),
            extra_buys=extra if win else 0, dedup_window_sec=win)
    except Exception as e:
        print(f"[insider] dedup check fail: {e}")
        labels = _insider_labels({(wallet or "").lower()} if wallet else set())
        return _insider_alert_text(pair, ca, _short_wallet(wallet, labels))


async def _alert_after_retry(token_ca: str, insider_addr: str,
                             delay: float = 0):
    """Alert-only: ulangi dx.best_pair 1x setelah `delay` detik.

    Dijalankan Di TASK BACKGROUND — tracker loop tidak pernah memblok.
    Kalau pair ketemu setelah retry → alert normal dikirim ke Telegram.
    Kalau masih None → diem saja (log forensik). Tidak boleh raise.
    """
    try:
        await asyncio.sleep(_INSIDER_RETRY_DELAY if not delay else delay)
        try:
            pair = await asyncio.to_thread(dx.best_pair, token_ca)
        except Exception:
            pair = None
        if not pair:
            print(f"[insider] retry best_pair masih None: {token_ca} "
                  f"tetap tak ter-index Dexscreener — alert dibatalkan "
                  f"(wallet {insider_addr or '?'}, hanya di log).")
            return
        labels = _insider_labels(
            {insider_addr.lower()} if insider_addr else set())
        wallet_label = _short_wallet(insider_addr, labels)
        text = _dedup_check_send(insider_addr, token_ca, pair)
        if text is None:
            print(f"[insider] dedup: {token_ca[:10]}… ditahan (retry)")
            return
        print(f"[insider] ALERT (alert-only, retry {delay:.0f}s): "
              f"{wallet_label} beli {token_ca[:10]}… — dikirim ke "
              f"Telegram, TIDAK masuk pipa.")
        _deliver_alert(text)
    except asyncio.CancelledError:
        pass  # shutdown — biarkan
    except Exception as e:
        print(f"[insider] retry alert fail: {e}")


async def process_insider_buy(token_ca: str, insider_addr: str = "",
                              notify=None, pair=None):
    """Insider membeli token (deteksi _inspect_block/_inspect_logs).

    Mode insider_alert_only=1 (DEFAULT): kirim notif Telegram RINGKAS
    (wallet, simbol+CA, mcap/liq, chart) — TANPA masuk pipa listener
    (0 evaluasi buy, 0 posisi baru, 0 RPC tambahan: pair berasal dari
    respons Dexscreener _inspect_block yang sudah ada).

    Mode insider_alert_only=0: perilaku LAMA — CA diteruskan ke
    listener.process_ca (allow_buy=True, siklus penuh sinyal scanner).

    `pair` opsional: respons dx.best_pair dari caller (hemat 1 fetch).
    """
    alert_only = (db.get("insider_alert_only", str) or "1") == "1"
    if not alert_only:
        # ---------- Perilaku lama (fallback aman) ----------
        src = _src()
        print(f"[insider] CA to pipeline: {token_ca}")
        caller_id = insider_addr.lower() if insider_addr else None
        caller_name = (f"INSIDER:{insider_addr[:10]}"
                       if insider_addr else "INSIDER_TRACKER")
        # Pipa listener.process_ca async — panggil via task supaya tracker
        # tidak memblokir loop pemantauan blok.
        asyncio.create_task(listener.process_ca(
            token_ca, src, caller_id=caller_id, caller_name=caller_name,
            allow_buy=True))
        return
    # ---------- Alert-only (v16.8, KORI 1) ----------
    if pair is None:
        try:
            pair = await asyncio.to_thread(dx.best_pair, token_ca)
        except Exception:
            pair = None
    if pair is None:
        # Token tak ter-index Dexscreener (launch baru di v4 pool, transfer
        # langsung, dsb.) → alert akan berisik ('?', mcap $0, tanpa chart).
        # Jangan kirim — log forensik saja, lalu RETRY SEKALI dalam ~30s
        # di task background supaya loop tracker tidak terblok.
        print(f"[insider] best_pair None utk {token_ca} "
              f"(wallet {insider_addr or '?'}) — alert dibiyangkan; "
              f"retry 1x dalam {_INSIDER_RETRY_DELAY:.0f}s.")
        try:
            asyncio.create_task(
                _alert_after_retry(token_ca, insider_addr,
                                   _INSIDER_RETRY_DELAY))
        except RuntimeError as e:
            # Ev-lok luar loop utama — tanpa retry supaya aman.
            print(f"[insider] retry fail (no loop): {e} — "
                  f"CA {token_ca} wallet {insider_addr or '?'} tak teralert.")
        return
    labels = _insider_labels({insider_addr.lower()} if insider_addr else set())
    wallet_label = _short_wallet(insider_addr, labels)
    text = _dedup_check_send(insider_addr, token_ca, pair)
    if text is None:
        print(f"[insider] dedup: {wallet_label} beli {token_ca[:10]}… "
              f"ditahan (sudah dialert dalam jendela)")
        return
    print(f"[insider] ALERT (alert-only): {wallet_label} beli "
          f"{token_ca[:10]}… — dikirim ke Telegram, TIDAK masuk pipa.")
    _deliver_alert(text)


# ============================ SCAN BLOK ============================

def pool_too_old(pair, max_age_sec, now_ms=None):
    """(too_old, age_sec) dari respons Dexscreener.

    Fail-OPEN: pair/created tidak ada / tidak bisa diparse → (False, None)
    (jangan memblokir sinyal karena data tak tersedia). `pairCreatedAt`
    Dexscreener dalam MILIDETIK.
    """
    try:
        if not pair or not isinstance(pair, dict):
            return False, None
        created = pair.get("pairCreatedAt")
        if created is None or created == "":
            return False, None
        created_ms = float(created)
        now_val = now_ms if now_ms is not None else time.time() * 1000
        age_sec = (float(now_val) - created_ms) / 1000.0
        if age_sec != age_sec:  # NaN guard
            return False, None
        return age_sec > float(max_age_sec), age_sec
    except (TypeError, ValueError):
        return False, None


async def _inspect_block(w3, block_number: int):
    """Periksa satu blok: cari tx dari wallet insider ke router swap → pipa.

    Return jumlah sinyal yang dikirim (untuk stats/mode dry-run).
    """
    insiders = set(db.get_active_insiders())
    if not insiders:
        return 0
    try:
        block = await asyncio.to_thread(
            w3.eth.get_block, block_number, full_transactions=True)
    except Exception as e:
        print(f"[tracker] get_block({block_number}) fail: {e}")
        return 0
    sent = 0
    for tx in (block.get("transactions") or []):
        frm = (tx.get("from") or "").lower()
        if frm not in insiders:
            continue
        to = (tx.get("to") or "").lower()
        if to != SWAP_ROUTER02:
            continue
        data = (tx.get("input") or b"")
        if hasattr(data, "hex"):
            data_hex = "0x" + data.hex()
        else:
            data_hex = str(data)
        if len(data_hex) < 12:  # naked transfer / no sel
            continue
        sel = data_hex[:10]
        if sel not in (EXACT_INPUT_SINGLE, EXACT_INPUT, MULTICALL):
            print(f"[tracker] insider {frm[:12]}… ke router, selector tak dikenal: {sel}")
            continue
        if not is_buy_call(data_hex):
            print(f"[tracker] insider {frm[:12]}… SELL (tokenIn bukan quote) — skip")
            continue
        token_out = extract_token_out(data_hex)
        if not token_out or token_out in QUOTE_ASSETS:
            print(f"[tracker] insider {frm[:12]}… tokenOut tak terdeteksi — skip")
            continue
        # 🔒 Proteksi slippage: bandingkan harga DEX sekarang vs N detik lalu.
        # Karena tracker REAKTIF (tanpa cache harga), cukup cek priceChange m5
        # Dexscreener — kalau harga sudah lari tajam, lewati (insider buy di
        # awal; kita ikut di harga biasanya sudah naik).
        pair = None
        try:
            pair = await asyncio.to_thread(dx.best_pair, token_out)
            # 🔒 Gate UMUR POOL — HANYA jalur legacy (insider_alert_only=0,
            # sniper butuh launch baru). Jalur alert-only TIDAK membuang
            # token berumur: paus beli koin lama = intel tetap dikirim.
            if (db.get("insider_alert_only", str) or "1") != "1":
                max_age = float(db.get("goldilocks_max_age_sec", float) or 300)
                too_old, age = pool_too_old(pair, max_age)
                if too_old:
                    print(f"[tracker] skip {token_out[:10]}… umur {age:.0f}s > "
                          f"{max_age:.0f}s (legacy: bukan launch baru)")
                    continue
            pc = (((pair or {}).get("priceChange") or {}).get("m5")) or 0.0
            if float(pc) > MAX_SLIP_PCT:
                print(f"[tracker] beta pusing: 'insider buy {token_out[:10]}… "
                      f"skip — harga sudah +{pc:.1f}% dalam 5m (slippage guard)")
                continue
        except Exception:
            pass  # tebusan data harga gagal → jangan block-trade
        try:
            await process_insider_buy(token_out, insider_addr=frm,
                                      pair=pair)
            sent += 1
        except Exception as e:
            print(f"[tracker] process_insider_buy fail: {e}")
    return sent


# ============================ BATCH LOG SCAN ============================

def _insider_to_topic2(insiders: set) -> list:
    """Susun padded address 32-byte utk topic2 (address indexed `to`)."""
    return ["0x000000000000000000000000" + a.lower().replace("0x", "")
            for a in insiders]


def _topic_addr(topic) -> str:
    """Ekstrak alamat 20B dari sebuah log topic (HexBytes/HexStr/str)."""
    s = topic.hex() if hasattr(topic, "hex") else str(topic)
    return "0x" + s[-40:].lower()


async def _inspect_logs(w3, from_block: int, to_block: int) -> int:
    """Pindai [from_block..to_block] dalam SATU eth_getLogs call:
    cari event ERC20 Transfer ke wallet insider → pipa process_insider_buy.

    Return jumlah sinyal yang dikirim. Dedup per-call: satu token (CA)
    hanya dikirim sekali per siklus batch meski muncul di banyak log,
    dan event kecil token transfer (bukan swap router) tetap menuju pipa.
    """
    insiders = set(db.get_active_insiders())
    if not insiders:
        return 0

    padded = _insider_to_topic2(insiders)
    filt = {
        "fromBlock": from_block,
        "toBlock": to_block,
        "topics": [TRANSFER_TOPIC, None, padded],
    }
    try:
        logs = await asyncio.to_thread(w3.eth.get_logs, filt)
    except Exception as e:
        print(f"[tracker] get_logs [{from_block}..{to_block}] fail: {e}")
        return 0

    sent = 0
    seen_tokens = set()
    for log in (logs or []):
        token_ca = (log.get("address") or "").lower()
        if not token_ca or token_ca in QUOTE_ASSETS:
            continue          # WETH/USDG transfer = SELL insider / unwrap
        if token_ca in seen_tokens:
            continue          # dedup batch — cukup sinyal sekali per CA
        to_hex = log["topics"][2].hex() if hasattr(log["topics"][2], "hex") \
            else str(log["topics"][2])
        insider_addr = "0x" + to_hex[-40:].lower()
        if insider_addr not in insiders:
            continue
        # 🔒 slippage guard: harga sudah lari >MAX_SLIP_PCT% → skip
        pair = None
        try:
            pair = await asyncio.to_thread(dx.best_pair, token_ca)
            pc = (((pair or {}).get("priceChange") or {}).get("m5")) or 0.0
            if float(pc) > MAX_SLIP_PCT:
                print(f"[tracker] beta pusing: 'insider buy "
                      f"{token_ca[:10]}… skip — harga sudah +{pc:.1f}% "
                      f"dalam 5m (slippage guard)")
                continue
        except Exception:
            pass  # data harga gagal → jangan block-trade
        try:
            await process_insider_buy(token_ca, insider_addr=insider_addr,
                                      pair=pair)
            seen_tokens.add(token_ca)
            sent += 1
        except Exception as e:
            print(f"[tracker] process_insider_buy fail: {e}")
    return sent


# ============================ LOOP UTAMA ============================

async def tracker_loop():
    """Loop pemantauan blok — task background bot.py. Matikan pakai setting
       tracker_on=0 di DB; aktifkan dengan tracker_on=1. Restart-safe: last
       inspected block disimpan di setting tracker_last_block."""
    print("[tracker] INSIDER TRACKER loop dimulai")
    from web3 import Web3
    from web3.providers import HTTPProvider

    # Probe chain berulang — sampai provider siap (RPC bisa belum siap saat
    # bot.py baru jalan).
    await asyncio.sleep(1.0)
    w3 = None
    try:
        w3 = Web3(HTTPProvider(config.RPC_URL))
        chain_id = await asyncio.to_thread(lambda: w3.eth.chain_id)
        if chain_id != config.CHAIN_ID:
            print(f"[tracker] ⚠️ chain_id RPC = {chain_id}, "
                  f"harusnya {config.CHAIN_ID} (Robinhood Chain)")
    except Exception as e:
        print(f"[tracker] RPC belum siap: {e}")

    last_num = int(db.get("tracker_last_block") or 0)

    while True:
        try:
            if db.get("tracker_on", str) != "1":
                await asyncio.sleep(POLL_IDLE)
                continue
            if w3 is None:
                w3 = Web3(HTTPProvider(config.RPC_URL))
            latest = await asyncio.to_thread(lambda: w3.eth.block_number)
            if latest <= last_num:
                await asyncio.sleep(POLL_IDLE)
                continue

            # Proteksi kalau tertinggal terlalu jauh (baru start / bot mati
            # lama): fast-forward, tapi tetap pindai CATCHUP_KEEP blok akhir.
            if latest - last_num > CATCHUP_LIMIT:
                print(f"[tracker] tertinggal {latest - last_num} blok — "
                      f"fast-forward ke {latest - CATCHUP_KEEP}")
                last_num = latest - CATCHUP_KEEP

            # Batch scan: satu eth_getLogs mengecek hingga BATCH_MAX_BLOCKS
            # blok (anti-lag; benchmark riil 50–300 blok ≈ 250ms 1 RPC call).
            from_block = last_num + 1
            to_block = min(latest, from_block + BATCH_MAX_BLOCKS - 1)
            try:
                await _inspect_logs(w3, from_block, to_block)
            except Exception as e:
                print(f"[tracker] inspect_logs "
                      f"[{from_block}..{to_block}] fail: {e}")
            db.set_("tracker_last_block", to_block)
            last_num = to_block
            # 2.5s ≈ ~25 blok baru di Robinhood Chain (~10.6 blk/dtk)
            await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[tracker] loop error: {e}")
            w3 = None
            await asyncio.sleep(3.0)


# ---------------------------------------------------------------
# 5. AUTO-SCAN AWAL DI KONDISI INSTALASI BARU (tabel insider kosong)
# ---------------------------------------------------------------

ROTATE_INTERVAL_SEC = 21600  # 6 jam — percepat rotasi insider: memcoin exit liquiditas cepat


def bind_notify(notify):
    """Ikat callback Telegram (async fn(text)) dari bot.py supaya scan awal
       instalasi baru bisa melaporkan ringkasannya ke admin via Telegram."""
    global _notify_cb
    _notify_cb = notify


_notify_cb = None


async def _notify_async(text: str):
    cb = _notify_cb
    if cb is None:
        return
    try:
        await cb(text)
    except Exception as e:
        print(f"[tracker] notify fail: {e}")


async def _initial_scan_if_empty():
    """Panggil sekali di awal loop: kalau tabel insider kosong (instalasi
       baru / git clone pertama), langsung scan koin runner on-chain dan
       recruit insider awal — tracker tidak jalan upgrade sendirian."""
    active_count = len(db.get_active_insiders())
    if active_count > 0:
        return
    print("[tracker] 🕵️ Instalasi baru terdeteksi (0 insider aktif) — "
          "memicu scan awal runner on-chain sekarang...")
    await _notify_async(
        "🕵️ Instalasi baru terdeteksi (0 insider aktif) — "
        "scan awal runner on-chain dimulai...")
    try:
        from tools.rotate_insiders import rotate
        summary = await asyncio.to_thread(rotate, 15)
    except Exception as e:
        print(f"[tracker] scan awal gagal: {e}")
        await _notify_async(f"[tracker] scan awal gagal: {e}")
        return
    added = summary.get("added", 0)
    total = summary.get("total_active", 0)
    print(f"[tracker] 🔄 Scan awal selesai: +{added} wallet baru dijaring "
          f"(total aktif: {total})")
    await _notify_async(
        f"🔄 Scan awal selesai: +{added} wallet baru dijaring "
        f"(total aktif: {total})")


async def auto_rotation_loop(notify=None):
    """Loop background rotasi wallet insider tiap 6 jam (non-blocking):
    prune wallet boncos/lunglai + daftar insider baru dari koin runner
    terbaru. On/off via setting tracker_auto_rotate (default "1").

    Semua kerjaannya sync/network → asyncio.to_thread supaya loop blok
    tracker tidak sempat tertahan.
    """
    if notify is not None:
        bind_notify(notify)
    print("[tracker] 🔄 auto rotation loop dimulai (interval 6h)")
    try:
        await _initial_scan_if_empty()
    except Exception as e:
        print(f"[tracker] initial scan error: {e}")
    while True:
        try:
            if db.get("tracker_auto_rotate", str) == "1":
                from tools.rotate_insiders import rotate
                summary = await asyncio.to_thread(rotate)
                print(f"[tracker] 🔄 rotasi insider: {summary}")
        except Exception as e:
            print(f"[tracker] rotation error: {e}")
        await asyncio.sleep(ROTATE_INTERVAL_SEC)
