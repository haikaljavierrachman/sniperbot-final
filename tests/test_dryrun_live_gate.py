"""Regresi lubang fallback MODE LATIHAN — "Gagal buy: saldo ETH kurang".

Kejadian nyata (2026-10-29, WOFL): mode latihan (agent_dry_run=1) TAPI
entry_mode='mata' dengan Mata OFF (agent_on=0) → tidak ada keputusan
sama sekali → pagar fallback lama tidak berlaku (ia mensyaratkan
agent_on=1) → kode jatuh ke trader.buy LIVE → gagal di cek saldo ETH.

Yang diuji di sini:
1. dry_run=1 + tanpa keputusan → process_ca berhenti "dry_run",
   trader.buy TIDAK PERNAH dipanggil, notif "buy LIVE ditahan" terkirim.
2. dry_run=0 (LIVE) + tanpa keputusan → jalur lama tetap jalan:
   trader.buy dipanggil, status "bought" (pagar tidak mengubah LIVE).
"""
import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["DB_PATH"] = _tmpdb.name

from sniper import db, listener  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))


db.init()
db.set_("min_liq_usd", "2000")
db.set_("scanner_ignore_v3", "1")
db.set_("entry_mode", "mata")
db.set_("agent_on", "0")        # Mata OFF → tidak ada keputusan

SRC = {"id": 1, "name": "SCANNER", "mcap_max": 99999.0}

_notes = []


async def _fake_notify(text, *a, **kw):
    _notes.append(text)


class FakeTrader:
    addr = "0x0000000000000000000000000000000000000000"

    def __init__(self):
        self.buy_calls = 0

    def buy(self, ca, pair, source_name, usd_override=None):
        self.buy_calls += 1
        return {
            "ca": ca.lower(), "symbol": "WOFLX", "name": "Wofl X",
            "pair_address": pair["pairAddress"], "chain_slug": "robinhood",
            "pool_type": "v4", "fee": 10000,
            "entry_price_usd": 0.0001, "tokens_total": 50000.0,
            "tokens_left": 50000.0, "eth_spent": 0.002,
            "usd_spent": 5.0, "honeypot": 0,
            "source_name": source_name, "buy_tx": "0xdeadbeef",
        }


def _pair():
    return {
        "chainId": "robinhood",
        "pairAddress": "0x" + "d" * 40,
        "labels": ["v4"],
        "baseToken": {"symbol": "WOFLX", "name": "Wofl X"},
        "liquidity": {"usd": 26703.0},
        "marketCap": 15888.0,
        "priceUsd": "0.0001",
        "priceNative": "0.00000004",
        "pairCreatedAt": int(time.time() * 1000) - 120000,
    }


_trader = FakeTrader()
_saved = (listener._notify, listener._public_notify, listener._trader,
          listener.dx.best_pair, listener.dx.social_ok,
          listener.safety.check)
listener._notify = _fake_notify
listener._public_notify = None
listener._trader = _trader
listener.dx.best_pair = lambda ca: _pair()
listener.dx.social_ok = lambda pair: (True, "")
listener.safety.check = lambda tr, ca, p: (True, "ok")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------- 1) latihan ditahan
print("\n=== 1. dry_run=1 + Mata OFF: buy LIVE harus DITAHAN ===")
db.set_("agent_dry_run", "1")
CA1 = "0x" + "a" * 40
_notes.clear()
s1 = run(listener.process_ca(CA1, SRC, None, "SCANNER"))
check("status dry_run", s1 == "dry_run", s1)
check("trader.buy TIDAK dipanggil", _trader.buy_calls == 0,
      f"buy_calls={_trader.buy_calls}")
check("notif 'buy LIVE ditahan' terkirim",
      any("buy LIVE ditahan" in t for t in _notes), str(_notes))
check("tidak ada notif 'Gagal buy'",
      not any("Gagal buy" in t for t in _notes), str(_notes))

# ---------------------------------------------------- 2) LIVE tidak berubah
print("\n=== 2. dry_run=0 (LIVE): jalur lama tetap beli ===")
db.set_("agent_dry_run", "0")
CA2 = "0x" + "b" * 40
_notes.clear()
s2 = run(listener.process_ca(CA2, SRC, None, "SCANNER"))
check("status bought", s2 == "bought", s2)
check("trader.buy dipanggil 1x", _trader.buy_calls == 1,
      f"buy_calls={_trader.buy_calls}")

# ---------------------------------------------------------------- restore
(listener._notify, listener._public_notify, listener._trader,
 listener.dx.best_pair, listener.dx.social_ok,
 listener.safety.check) = _saved

print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:", FAIL)
    sys.exit(1)
