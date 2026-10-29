"""Regresi closed_trail palsu (#40/#44/#50): peak & trailing harus pakai harga
turunan kuoter on-chain, bukan harga layar Dexscreener yang basi.
Jalankan: python3 tests/test_trail_onchain_price.py (DB temporari)."""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_t.close()
os.environ["DB_PATH"] = _t.name

from sniper import db, engine  # noqa: E402

db.init()
db.reset_paper_cash(100.0)
db.set_("max_hold_sec", "999999")
db.set_("trail_on", "1")
db.set_("trail_pct", "0.25")
sold = []
FAIL = []


async def notify(text, buttons=None, **kw):
    return text


async def fake_sell(pid, frac, reason):
    sold.append(reason)
    return "ok"


class Dec:
    def call(self): return 18


class Tok:
    class functions:
        @staticmethod
        def decimals(): return Dec()


class FakeTrader:
    w3 = None
    q = 0.0
    def erc20(self, a): return Tok()
    def quote_sell_eth(self, *a, **k): return self.q


ENTRY = 1e-4
tr = FakeTrader()
engine._trader, engine._notify, engine.execute_sell = tr, notify, fake_sell


def new_pos():
    db.open_position(ca="0x" + "ab" * 20, symbol="T", pair_address="0xP" + "0" * 20,
                     chain_slug="robinhood", pool_type="v4", fee=0, entry_price_usd=ENTRY,
                     tokens_total=1000.0, tokens_left=1000.0, eth_spent=1.0, usd_spent=5.0,
                     honeypot=0, source_name="UT", buy_tx="sim", is_simulated=1)
    pid = max(db.open_positions(), key=lambda p: p["id"])["id"]
    db.update_position(pid, tp1_done=1)  # sudah lewat TP1 → trailing aktif
    return pid


async def stale_pair(pos):  # layar basi: harga == entry, liq sehat
    return {"priceUsd": str(ENTRY), "priceNative": "1e-8", "liquidity": {"usd": 30000}}


engine.current_pair = stale_pair

# 1) on-chain +20% (di bawah TP2, di atas entry), layar basi = entry
pid = new_pos(); tr.q = 1.20
asyncio.run(engine._check(dict(db.position(pid))))
pk = db.position(pid)["peak_price"]
if not (pk and abs(pk - ENTRY * 1.2) < 1e-12): FAIL.append(f"peak harus {ENTRY*1.2}, dapat {pk}")
if sold: FAIL.append(f"tidak boleh jual saat on-chain +20%: {sold}")

# 2) on-chain turun ke +5% dari peak +20% (< peak*0.75=+-10%? 1.05 > 0.9) → tidak trail
tr.q = 1.05
asyncio.run(engine._check(dict(db.position(pid))))
if sold: FAIL.append(f"+5% > stop 0.9x peak, tidak boleh trail: {sold}")

# 3) peak 2.0x lalu on-chain 1.4x (< 1.5 = 0.75*2.0) → trail kena
db.update_position(pid, peak_price=ENTRY * 2.0); tr.q = 1.4
asyncio.run(engine._check(dict(db.position(pid))))
if sold != ["trail"]: FAIL.append(f"harus trail saat jatuh di bawah 0.75*peak: {sold}")

print("FAIL:", FAIL) if FAIL else print("OK test_trail_onchain_price (3/3)")
sys.exit(1 if FAIL else 0)
