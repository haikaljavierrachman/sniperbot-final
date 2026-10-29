#!/usr/bin/env python3
"""Tes logika v16 GABUNGAN — TANPA jaringan, TANPA LLM, TANPA Telegram asli.

Dua sumber sinyal (listener grup + scanner) hidup dalam SATU proses & SATU
DB. Yang dibuktikan di sini:

  1-5. Logika scanner warisan v14/v15: parse PoolCreated, filter pasar,
       skor, cap per jam, inisialisasi watermark factory dari head.
  6.   ATURAN MODE jalur scanner (semantik gabungan = v15): allow_buy=True +
       agent_dry_run=1 → BUY Mata berhenti di "dry_run" (buy TIDAK jalan);
       dry_run=0 → buy DIEKSEKUSI; mekanisme allow_buy=False tetap utuh.
  7.   DEDUPE LINTAS-SUMBER: CA yang sama tidak boleh diproses dua kali
       walau ditemukan dua sumber berbeda. Listener grup duluan → jalur
       scanner jadi "dup" (dan sebaliknya); calls & decisions di DB tetap
       masing-masing SATU, tercatat atas nama sumber yang datang pertama.
  8.   Startup GABUNGAN: validate() MENOLAK bila TG_API_ID tidak ada
       (hybrid punya userbot — beda dari v15), dan LOLOS bila lengkap.

Semua dependensi eksternal (Dexscreener, safety, agent/LLM, trader, client
Telegram) diganti fake. DB diarahkan ke file temp sebelum modul sniper
di-import.

Pakai:  python test_scanner_logic.py     (exit 0 = semua lolos)
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

# --- DB temp SEBELUM import sniper (config membaca env saat import) ---
_TMPDIR = tempfile.mkdtemp(prefix="scan_logic_v16_")
os.environ["DB_PATH"] = os.path.join(_TMPDIR, "test.db")

from sniper import config, db, listener, scanner  # noqa: E402

V16_ROOT = os.path.dirname(os.path.abspath(__file__))
HASIL = []


def cek(nama, kondisi, detail=""):
    HASIL.append((nama, bool(kondisi)))
    print(f"{'✅' if kondisi else '❌'} {nama}" + (f" — {detail}" if detail and not kondisi else ""))


NOW_MS = int(time.time() * 1000)
TOKEN = "0x" + "11" * 20
FAKE_CA = "0x" + "ab" * 20
FAKE_CA2 = "0x" + "ef" * 20
FAKE_CA3 = "0x" + "cd" * 20
CA_D1 = "0x" + "aa" * 20   # dedupe arah 1: listener duluan
CA_D2 = "0x" + "bb" * 20   # dedupe arah 2: scanner duluan


def fake_pair(**ubah):
    p = {
        "chainId": "robinhood",
        "pairAddress": "0x" + "cd" * 20,
        "baseToken": {"address": FAKE_CA, "symbol": "TEST", "name": "Test Token"},
        "priceUsd": "0.001", "priceNative": "0.0000004",
        "marketCap": 50000, "fdv": 50000,
        "liquidity": {"usd": 20000},
        "volume": {"h1": 5000, "h24": 20000},
        "txns": {"h1": {"buys": 40, "sells": 20}, "h24": {"buys": 100, "sells": 80}},
        "pairCreatedAt": NOW_MS - 30 * 60000,
        "info": {"websites": [{"label": "Website", "url": "https://example.com"}],
                 "socials": [{"type": "twitter", "url": "https://x.com/test"}]},
        "labels": ["v3"],
    }
    p.update(ubah)
    return p


# ================= 1. parse_pool_created_log =================
def _topic(addr):
    return "0x" + "0" * 24 + addr[2:]


log1 = {"topics": ["0x" + "00" * 32, _topic(scanner.WETH_ADDR), _topic(TOKEN)]}
cek("parse: token1 non-quote diambil", scanner.parse_pool_created_log(log1) == TOKEN.lower())
log2 = {"topics": ["0x" + "00" * 32, _topic(TOKEN), _topic(scanner.USDG_ADDR)]}
cek("parse: token0 non-quote diambil", scanner.parse_pool_created_log(log2) == TOKEN.lower())
log3 = {"topics": ["0x" + "00" * 32, _topic(scanner.WETH_ADDR), _topic(scanner.USDG_ADDR)]}
cek("parse: dua sisi quote -> None", scanner.parse_pool_created_log(log3) is None)
cek("parse: topics kurang -> None", scanner.parse_pool_created_log({"topics": []}) is None)

# ================= 2. market_ok =================
db.init()
ok, _ = scanner.market_ok(fake_pair())
cek("market_ok: pair bagus lolos", ok)
ok, r = scanner.market_ok(fake_pair(liquidity={"usd": 100}))
cek("market_ok: liq rendah ditolak", not ok and r.startswith("liq_rendah"), r)
ok, r = scanner.market_ok(fake_pair(pairCreatedAt=NOW_MS - 2000 * 60000))
cek("market_ok: pair tua ditolak", not ok and r.startswith("terlalu_tua"), r)
ok, r = scanner.market_ok(fake_pair(volume={"h1": 10, "h24": 10}))
cek("market_ok: volume rendah ditolak", not ok and r.startswith("vol_rendah"), r)
ok, r = scanner.market_ok(fake_pair(txns={"h1": {"buys": 2, "sells": 1}}))
cek("market_ok: transaksi sepi ditolak", not ok and r.startswith("txns_sepi"), r)

# ================= 3. score_pair =================
bagus = fake_pair(volume={"h1": 50000}, liquidity={"usd": 100000})
sepi = fake_pair(volume={"h1": 100}, liquidity={"usd": 6000})
cek("score: pair ramai > pair sepi", scanner.score_pair(bagus) > scanner.score_pair(sepi))

# ================= 4. cap per jam =================
t0 = time.time()
scanner._decision_ts.clear()
for _ in range(3):
    scanner._note_decision(now=t0)
cek("cap: 3 keputusan dari kuota 5 -> sisa 2", scanner.cap_left(now=t0) == 2)
scanner._note_decision(now=t0)
scanner._note_decision(now=t0)
cek("cap: kuota habis -> 0", scanner.cap_left(now=t0) == 0)
scanner._decision_ts.clear()
scanner._note_decision(now=t0 - 3700)  # sudah lewat 1 jam → tidak dihitung
cek("cap: stempel kedaluwarsa tidak dihitung", scanner.cap_left(now=t0) == 5)
scanner._decision_ts.clear()

# ================= 5. watermark factory: mulai dari head =================
class _FakeEth:
    block_number = 123456

    @staticmethod
    def get_logs(_args):
        return []


class _FakeW3:
    eth = _FakeEth()


db.set_("scanner_last_block", "0")  # paksa kondisi pertama kali jalan
fc = scanner.fetch_factory_candidates(_FakeW3())
cek("factory: siklus pertama -> [] tanpa error", fc == [])
cek("factory: watermark dilompatkan ke head (123456)",
    int(db.get("scanner_last_block")) == 123456,
    f"watermark={db.get('scanner_last_block')}")
fc2 = scanner.fetch_factory_candidates(_FakeW3())  # head sama → tidak ada blok baru
cek("factory: siklus kedua (head sama) -> []", fc2 == [])

# ================= fakes bersama (dipakai bagian 6 & 7) =================
db.set_("agent_on", "1")
config.LLM_API_KEY = "kunci-tes-bukan-llm-sungguhan"

KEPUTUSAN_BUY = {
    "action": "BUY", "conviction": 90, "size_mult": 1.0,
    "tp1": 0.5, "tp2": 1.0, "sl": -0.3,
    "reason": "fake: momentum bagus", "risks": "fake",
    "blacklist_caller": False,
}


class FakeTrader:
    def __init__(self):
        self.buy_calls = 0

    def buy(self, ca, pair, source_name="", usd_override=None):
        self.buy_calls += 1
        return {
            "ca": ca.lower(), "symbol": "TEST",
            "pair_address": pair["pairAddress"], "chain_slug": pair["chainId"],
            "pool_type": "v3", "fee": 3000,
            "entry_price_usd": 0.001, "tokens_total": 1000.0, "tokens_left": 1000.0,
            "eth_spent": 0.001, "usd_spent": 2.5, "honeypot": 0,
            "source_name": source_name, "buy_tx": "0xfake",
        }


notif = []


async def fake_notify(text, buttons=None):
    notif.append(text)


trader_palsu = FakeTrader()
listener._trader = trader_palsu
listener._notify = fake_notify
listener.dx.best_pair = lambda ca: fake_pair()
listener.safety.check = lambda trader, ca, pair: (True, "aman (fake)")
listener.agent.decide_entry = lambda pair, ca, src, note, caller=None: dict(KEPUTUSAN_BUY)

SRC = {"id": 99, "name": "SCANNER", "mcap_max": 100000.0}

# ================= 6. ATURAN MODE jalur scanner (gabungan) =================
# (a) allow_buy=True + dry_run ON → tertahan dry-run global, buy TIDAK jalan.
db.set_("agent_dry_run", "1")
status = asyncio.run(listener.process_ca(FAKE_CA, SRC, None, "SCANNER", allow_buy=True))
cek("mode(a): dry_run ON -> status dry_run", status == "dry_run", status)
cek("mode(a): trader.buy TIDAK dipanggil", trader_palsu.buy_calls == 0,
    f"buy_calls={trader_palsu.buy_calls}")
cek("mode(a): notif MODE LATIHAN menyebut SCANNER",
    any("MODE LATIHAN" in n and "SCANNER" in n for n in notif))

# (b) Kontrol: setup sama, dry_run OFF → buy DIEKSEKUSI.
db.set_("agent_dry_run", "0")
status = asyncio.run(listener.process_ca(FAKE_CA2, SRC, None, "SCANNER", allow_buy=True))
cek("mode(b): dry_run OFF -> status bought", status == "bought", status)
cek("mode(b): trader.buy dipanggil 1x", trader_palsu.buy_calls == 1,
    f"buy_calls={trader_palsu.buy_calls}")

# (c) Mekanisme pagar allow_buy=False tetap utuh (scanner gabungan tidak memakainya).
status = asyncio.run(listener.process_ca(FAKE_CA3, SRC, None, "SCANNER", allow_buy=False))
cek("mode(c): allow_buy=False -> no_buy_scanner", status == "no_buy_scanner", status)
cek("mode(c): buy tetap 1x (tidak bertambah)", trader_palsu.buy_calls == 1,
    f"buy_calls={trader_palsu.buy_calls}")

# ================= 7. DEDUPE LINTAS-SUMBER =================
db.set_("agent_dry_run", "1")  # mode latihan buat kedua arah dedupe
db.add_source(-100777001, 0, "GRUPTES", 100000.0)
grup = next(s for s in db.sources() if s["name"] == "GRUPTES")
scan_src = scanner.get_scanner_source()

# Fake agent yang meniru agent asli: keputusan DICATAT ke DB (seperti
# decide_entry beneran) + src yang diterimanya direkam buat cek atribusi.
decide_srcs = []


def fake_decide_logging(pair, ca, src, note, caller=None):
    decide_srcs.append(src["name"])
    db.log_decision(ca=ca, symbol="TEST", role="screener", action="BUY",
                    conviction=90, size_mult=1.0, reason="fake", risks="fake")
    return dict(KEPUTUSAN_BUY)


listener.agent.decide_entry = fake_decide_logging

# Bungkus process_ca: handler listener menelan return value _handle, jadi
# status tiap pemrosesan direkam lewat pembungkus ini.
status_log = []
_orig_process_ca = listener.process_ca


async def recording_process_ca(ca, src, caller_id, caller_name, allow_buy=True):
    st = await _orig_process_ca(ca, src, caller_id, caller_name, allow_buy=allow_buy)
    status_log.append(st)
    return st


listener.process_ca = recording_process_ca


# Client userbot PALSU: cukup menangkap handler dari decorator @client.on.
class FakeUserClient:
    def __init__(self):
        self.handler = None

    def on(self, _event_builder):
        def deco(fn):
            self.handler = fn
            return fn
        return deco


fake_client = FakeUserClient()
listener.register(fake_client, trader_palsu, fake_notify)
cek("dedupe: handler listener terdaftar di client palsu", fake_client.handler is not None)


def _count(sql, arg):
    c = sqlite3.connect(config.DB_PATH)
    try:
        return c.execute(sql, (arg,)).fetchone()[0]
    finally:
        c.close()


def calls_of(ca):
    return _count("SELECT COUNT(*) FROM calls WHERE ca=?", ca.lower())


def call_source_of(ca):
    c = sqlite3.connect(config.DB_PATH)
    try:
        r = c.execute("SELECT source_id FROM calls WHERE ca=?", (ca.lower(),)).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def decisions_of(ca):
    return _count("SELECT COUNT(*) FROM decisions WHERE ca=?", ca.lower())


class FakeEvent:
    """Tiruan events.NewMessage: cukup atribut yang disentuh handler/_handle."""

    def __init__(self, chat_id, text, sender_id=555000111):
        self.chat_id = chat_id
        self.message = SimpleNamespace(reply_to=None)
        self.raw_text = text
        self.sender_id = sender_id

    async def get_sender(self):
        return SimpleNamespace(username="tester", first_name="Tester")


buy_awal = trader_palsu.buy_calls

# --- Arah 1: LISTENER (grup) duluan, scanner belakangan → dup ---
ev1 = FakeEvent(-100777001, f"call nih {CA_D1} gas")
asyncio.run(fake_client.handler(ev1))
cek("dedupe-1: event grup diproses handler -> dry_run",
    status_log and status_log[-1] == "dry_run", str(status_log))
cek("dedupe-1: calls CA_D1 tepat 1", calls_of(CA_D1) == 1, f"calls={calls_of(CA_D1)}")
cek("dedupe-1: call tercatat atas nama source GRUP",
    call_source_of(CA_D1) == grup["id"], f"source_id={call_source_of(CA_D1)}")
cek("dedupe-1: decisions CA_D1 tepat 1", decisions_of(CA_D1) == 1,
    f"decisions={decisions_of(CA_D1)}")
cek("dedupe-1: Mata menerima src GRUPTES", decide_srcs and decide_srcs[-1] == "GRUPTES",
    str(decide_srcs))

st = asyncio.run(listener.process_ca(CA_D1, scan_src, None, "SCANNER", allow_buy=True))
cek("dedupe-1: jalur scanner CA sama -> dup", st == "dup", st)
cek("dedupe-1: calls tetap 1 setelah scanner", calls_of(CA_D1) == 1)
cek("dedupe-1: decisions tetap 1 setelah scanner", decisions_of(CA_D1) == 1)

# --- Arah 2: SCANNER duluan, listener (grup) belakangan → dup ---
st = asyncio.run(listener.process_ca(CA_D2, scan_src, None, "SCANNER", allow_buy=True))
cek("dedupe-2: jalur scanner diproses -> dry_run", st == "dry_run", st)
cek("dedupe-2: call tercatat atas nama source SCANNER",
    call_source_of(CA_D2) == scan_src["id"], f"source_id={call_source_of(CA_D2)}")

ev2 = FakeEvent(-100777001, f"eh CA yang sama nongol di grup {CA_D2}")
asyncio.run(fake_client.handler(ev2))
cek("dedupe-2: event grup CA sama -> dup (via handler)",
    status_log and status_log[-1] == "dup", str(status_log))
cek("dedupe-2: calls CA_D2 tetap 1", calls_of(CA_D2) == 1, f"calls={calls_of(CA_D2)}")
cek("dedupe-2: decisions CA_D2 tetap 1", decisions_of(CA_D2) == 1,
    f"decisions={decisions_of(CA_D2)}")
cek("dedupe: tidak ada buy sama sekali selama dedupe (dry_run)",
    trader_palsu.buy_calls == buy_awal,
    f"buy_calls={trader_palsu.buy_calls} vs awal={buy_awal}")

listener.process_ca = _orig_process_ca  # kembalikan

# ================= 8. startup GABUNGAN: TG_API_ID/HASH wajib =================
def _run_validate(extra_env, drop=()):
    env = dict(os.environ)
    for k in drop:
        env.pop(k, None)
    env.update(extra_env)
    env["DB_PATH"] = os.path.join(_TMPDIR, "startup.db")
    code = ("import os, sys; sys.path.insert(0, os.getcwd()); "
            "from sniper import config; config.validate(); print('VALIDATE_OK')")
    return subprocess.run([sys.executable, "-c", code], cwd=V16_ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


FULL_ENV = {"SWAP_ROUTER02": "0x" + "22" * 20, "TG_API_ID": "123456",
            "TG_API_HASH": "hash-tes", "BOT_TOKEN": "123:token-tes",
            "ADMIN_ID": "424242"}

r = _run_validate({k: v for k, v in FULL_ENV.items() if k != "TG_API_ID"},
                  drop=("TG_API_ID",))
cek("startup: validate() MENOLAK tanpa TG_API_ID (gabungan punya userbot)",
    r.returncode != 0 and "VALIDATE_OK" not in r.stdout,
    (r.stdout + r.stderr).strip()[-200:])

r = _run_validate(FULL_ENV)
cek("startup: validate() LOLOS bila env lengkap (termasuk TG_API_ID/HASH)",
    r.returncode == 0 and "VALIDATE_OK" in r.stdout,
    (r.stdout + r.stderr).strip()[-200:])

# ================= ringkasan =================
lolos = sum(1 for _, ok_ in HASIL if ok_)
print(f"\n=== {lolos}/{len(HASIL)} tes lolos ===")
sys.exit(0 if lolos == len(HASIL) else 1)
