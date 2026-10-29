"""Unit test: On-Chain Liquidity & Quoter Fallback untuk Rug Monitor (v16.5).

Uji:
1) _check: Dexscreener down (pair None) & quote on-chain 0 ETH
   → EMERGENCY EXIT reason=rug (posisi closed_rug).
2) _check: Dexscreener liq anjlok, tapi quote on-chain sehat
   (>= 50% expected spot) → false alarm DIBATALKAN, posisi tetap open.
3) _check: Dexscreener liq anjlok & quote on-chain drop
   (< 50% expected spot) → konfirmasi rug, EMERGENCY EXIT.
4) execute_sell reason="rug" pada posisi simulasi:
   quote on-chain 0 → eth_real 0.0 (net loss -100%), bukan angka layar basi.

Jalankan: python3 tests/test_onchain_rug_guard.py
(DB temporari — tidak menyentuh produksi.)
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["DB_PATH"] = _tmpdb.name

from sniper import db, engine            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(
        f"{name}{' — ' + detail if detail and not cond else ''}")
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))


db.init()
db.reset_paper_cash(100.0)

CA = "0x" + "ab" * 20
PAIR_ADDR = "0PAIRTEST" + "0" * 20
_notifications = []


def fake_notify(text, buttons=None, **kw):
    _notifications.append(text)
    return text


async def fake_notify_async(text, buttons=None, **kw):
    _notifications.append(text)
    return text


engine._notify = fake_notify_async


class FakeERC20:
    class functions:
        @staticmethod
        def decimals():
            class F:
                def call(self):
                    return 18
            return F()


class QuoteBox:
    """Handle quote_sell_eth yang bisa diarahkan per-skenario."""
    value = 0.0
    raise_boom = False

    def __call__(self, ca, ptype, fee, amount_raw, pool_id=None):
        if QuoteBox.raise_boom:
            raise RuntimeError("quoter revert")
        return QuoteBox.value


QUOTE = QuoteBox()


class FakeTrader:
    w3 = None

    def erc20(self, addr):
        return FakeERC20()

    def quote_sell_eth(self, ca, ptype, fee, amount_raw, pool_id=None):
        if QUOTE.raise_boom:
            raise RuntimeError("quoter revert")
        return QUOTE.value


# ====================================================================
print("\n[Setup] posisi simulasi open + task")
db.set_("rug_liq_drop", "0.5")        # drop > 50% = rug
db.set_("max_hold_sec", "999999")     # time stop OFF untuk test ini
db.set_("poll_seconds", "20")

def new_pos(symbol, pair_addr):
    """Buka posisi simulasi open, return id baris terbaru."""
    db.open_position(
        ca=CA, symbol=symbol, pair_address=pair_addr, chain_slug="robinhood",
        pool_type="v3", fee=3000, entry_price_usd=0.0011,
        tokens_total=1000.0, tokens_left=1000.0, eth_spent=1.0, usd_spent=5.0,
        honeypot=0, source_name="UNITTEST", buy_tx="simulated_buy",
        is_simulated=1)
    return max(db.open_positions(), key=lambda p: p["id"])["id"]


pos_id = new_pos("RGT", PAIR_ADDR)

engine._trader = FakeTrader()
engine._notify = fake_notify_async
engine._liq_last = {}
engine._thin_warned = set()
engine._cleanup_marks = set()
_liq_saved, _thin_saved, marks_saved = (
    engine._liq_last, engine._thin_warned, engine._cleanup_marks)

_pos = db.position(pos_id)


def sstatus():
    return db.position(pos_id)["status"]


# ====================================================================
print("\n[1] Dexscreener down + quote on-chain 0 → EMERGENCY EXIT rug")


def pair_none(pos):
    return None


async def pair_none_async(pos):
    return None


_saved_cp = engine.current_pair
engine.current_pair = pair_none_async
async def pair_none(pos):
    return None


engine.current_pair = pair_none
QUOTE.value, QuoteBox.raise_boom = 0.0, False
try:
    asyncio.run(engine._check(dict(_pos)))
    check("posisi ditutup closed_rug (Dex down + quote 0)",
          sstatus() == "closed_rug", sstatus())
    check("notif RUG PULL on-chain muncul",
          any("RUG PULL terdeteksi on-chain" in t for t in _notifications),
          str(_notifications[-1][:160]) if _notifications else "")
finally:
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# buka posisi baru untuk skenario berikutnya
pos_id2 = new_pos("RGT2", "0PAIR2" + "0" * 18)
_pos2 = db.position(pos_id2)

# ====================================================================
print("\n[2] Dexscreener liq drop, quote on-chain sehat → false alarm dibatalkan")
_notifications.clear()
pair_drop_healthy = {
    "priceUsd": "0.0010", "priceNative": "0.00055",  # PnL -9% (tidak kena SL 30%)
    "liquidity": {"usd": 2000},     # baseline 10k → drop 80% > threshold
}
_saved_cp = engine.current_pair


async def pair_ok(pos):
    return dict(pair_drop_healthy)


engine.current_pair = pair_ok
engine._liq_last[pos_id2] = 10000.0          # baseline sebelum drop
# quote sehat: ≥ 50% expected spot (0.275) DAN tidak men-trigger SL dari
# modal (cost 1.0 ETH): 0.9/1.0-1 = -10% — di atas SL -30%.
QUOTE.value, QUOTE.raise_boom = 0.9, False
try:
    asyncio.run(engine._check(dict(_pos2)))
    check("false alarm dibatalkan → status masih open",
          db.position(pos_id2)["status"] == "open", db.position(pos_id2)["status"])
    check("TIDAK ada notif EMERGENCY EXIT rug",
          not any("EMERGENCY EXIT" in t for t in _notifications),
          str(_notifications[-1][:160]) if _notifications else "")
finally:
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# ====================================================================
print("\n[3] Dexscreener liq drop + quote on-chain drop → rug KONFIRMASI")
_notifications.clear()
pair_drop = {
    "priceUsd": "0.0006", "priceNative": "0.00055",
    "liquidity": {"usd": 2000},     # baseline 10k → drop 80% > threshold
}
pos_id3 = new_pos("RGT3", "0PAIR3" + "0" * 18)
_pos3 = db.position(pos_id3)
_saved_cp = engine.current_pair


async def pair_ok(pos):
    return dict(pair_drop)


engine.current_pair = pair_ok
engine._liq_last[pos_id3] = 10000.0
QUOTE.value, QUOTE.raise_boom = 0.01, False   # jauh < 50% expected (0.275) → rug riil
try:
    asyncio.run(engine._check(dict(_pos3)))
    check("rug terkonfirmasi → closed_rug",
          db.position(pos_id3)["status"] == "closed_rug",
          db.position(pos_id3)["status"])
    check("notif liq anjlok + EMERGENCY EXIT muncul",
          any("EMERGENCY EXIT" in t for t in _notifications),
          str(_notifications[-1][:160]) if _notifications else "")
finally:
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# ====================================================================
print("\n[4] execute_sell reason=rug (simulasi) → eth_real 0 saat quote 0")
pos_id4 = new_pos("RGT4", "0PAIR4" + "0" * 18)
_spot_pnl_before = db.realized_total(pos_id4) if hasattr(
    db, "realized_total") else None
classified = {"eth_real": None}
_orig_add_realized = db.add_realized
db.add_realized = lambda pid, eth_out: classified.__setitem__(
    "eth_real", eth_out)
_saved_cp = engine.current_pair


async def pair_layar(pos):
    return {"priceUsd": "0.001", "priceNative": "0.00055",
            "liquidity": {"usd": 100}}


engine.current_pair = pair_layar
QUOTE.value, QuoteBox.raise_boom = 0.0, False
try:
    out = asyncio.run(engine.execute_sell(pos_id4, 1.0, "rug"))
    check("hasil sell rug = 0.0 ETH (bukan harga layar basi)",
          abs(classified["eth_real"]) < 1e-12, str(classified["eth_real"]))
    check("posisi closed_rug", db.position(pos_id4)["status"] == "closed_rug",
          db.position(pos_id4)["status"])
    check("teks hasil menyebut realized -100%",
          "-100.0%" in out or "-100%" in out, out[:200])
finally:
    db.add_realized = _orig_add_realized
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# ---- 4b) quote on-chain bernilai riil > 0 → gunakan quote itu ----
pos_id5 = new_pos("RGT5", "0PAIR5" + "0" * 18)
classified2 = {"eth_real": None}
db.add_realized = lambda pid, eth_out: classified2.__setitem__(
    "eth_real", eth_out)
_engine_cp = engine.current_pair


async def pair_layar2(pos):
    return {"priceUsd": "0.001", "priceNative": "0.00055",   # layar → 0.55 ETH
            "liquidity": {"usd": 100}}


engine.current_pair = pair_layar2
QUOTE.value, QUOTE.raise_boom = 0.05, False   # on-chain cuma 0.05 ETH
try:
    out = asyncio.run(engine.execute_sell(pos_id5, 1.0, "rug"))
    check("eth_real diambil dari quote on-chain (0.05), bukan layar (0.55)",
          abs(classified2["eth_real"] - 0.05) < 1e-9,
          str(classified2["eth_real"]))
    check("realized rug riil ≈ -95%",
          "-95.0%" in out or "-95%" in out, out[:200])
finally:
    db.add_realized = _orig_add_realized
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# ---- 4c) quoter on-chain revert saat rug → eth_real 0 ----
pos_id6 = new_pos("RGT6", "0PAIR6" + "0" * 18)
classified3 = {"eth_real": None}
db.add_realized = lambda pid, eth_out: classified3.__setitem__(
    "eth_real", eth_out)
engine.current_pair = _saved_cp


async def pair_layar3(pos):
    return {"priceUsd": "0.001", "priceNative": "0.00055",
            "liquidity": {"usd": 100}}


engine.current_pair = pair_layar3
QUOTE.value, QUOTE.raise_boom = 0.5, True   # revert!
try:
    asyncio.run(engine.execute_sell(pos_id6, 1.0, "rug"))
    check("quoter revert saat rug → eth_real 0.0 (pool mati)",
          abs(classified3["eth_real"]) < 1e-12, str(classified3["eth_real"]))
finally:
    db.add_realized = _orig_add_realized
    engine.current_pair = _saved_cp
    engine._liq_last = _liq_saved
    engine._thin_warned = _thin_saved
    engine._cleanup_marks = marks_saved

# ====================================================================
print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("SEMUA TES LULUS.")
