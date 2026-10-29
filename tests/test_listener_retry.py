"""Unit test jalur retry listener (antrean pending scanner).

Aturan yang diuji:
1. Panggilan normal: CA kedua kali → "dup" (dedup permanen).
2. retry=True: boleh dievaluasi ulang walau sudah tercatat di `calls`,
   TAPI tidak boleh membuka posisi kedua di CA yang sama ("dup_position").
3. Filter mcap/liq di listener sekarang mengirim notif SKIP (tidak senyap).
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

from sniper import config, db, listener  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))


db.init()
db.set_("min_liq_usd", "2000")
db.set_("scanner_ignore_v3", "1")

CA = "0x" + "a" * 40
SRC = {"id": 1, "name": "SCANNER", "mcap_max": 99999.0}

_notes = []


async def _fake_notify(text, *a, **kw):
    _notes.append(text)


_orig_best_pair = listener.dx.best_pair
_orig_notify = listener._notify
listener._notify = _fake_notify


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 1) dedup
print("\n=== 1. dedup normal vs retry ===")
listener.dx.best_pair = lambda ca: None
s1 = run(listener.process_ca(CA, SRC, None, "SCANNER"))
check("panggilan pertama: diproses (bukan dup)", s1 == "filtered:no_pair", s1)
s2 = run(listener.process_ca(CA, SRC, None, "SCANNER"))
check("panggilan kedua tanpa retry: dup", s2 == "dup", s2)
s3 = run(listener.process_ca(CA, SRC, None, "SCANNER", retry=True))
check("retry=True: TIDAK dianggap dup", s3 == "filtered:no_pair", s3)

# ---------------------------------------------------- 2) jangan dobel posisi
print("\n=== 2. retry tidak boleh buka posisi kedua ===")
db.open_position(
    ca=CA, symbol="TST", pair_address="0x" + "b" * 40, chain_slug="robinhood",
    pool_type="v4", fee=10000, entry_price_usd=1.0, tokens_total=1.0,
    tokens_left=1.0, eth_spent=0.001, usd_spent=5.0, honeypot=0,
    source_name="SCANNER")
s4 = run(listener.process_ca(CA, SRC, None, "SCANNER", retry=True))
check("retry + posisi open di CA sama: dup_position", s4 == "dup_position", s4)

# --------------------------------------------- 3) filter liq/mcap bersuara
print("\n=== 3. filter liq/mcap kirim notif SKIP ===")
CA2 = "0x" + "c" * 40
pair_liq_low = {
    "pairAddress": "0x" + "d" * 64,
    "labels": ["v4"],
    "baseToken": {"symbol": "NEWB"},
    "liquidity": {"usd": 1459.0},
    "marketCap": 12000.0,
    "priceUsd": "0.0001",
    "priceNative": "0.00000004",
}
listener.dx.best_pair = lambda ca: pair_liq_low if ca.lower() == CA2 else None
_notes.clear()
s5 = run(listener.process_ca(CA2, SRC, None, "SCANNER"))
check("liq < min → filtered:liq", s5 == "filtered:liq", s5)
check("liq rendah mengirim notif", len(_notes) == 1 and "liq_rendah" in _notes[0],
      str(_notes))
check("notif memuat angka liq & ambang",
      "$1,459" in _notes[0] and "$2,000" in _notes[0], str(_notes))

CA3 = "0x" + "e" * 40
pair_mcap_big = dict(pair_liq_low)
pair_mcap_big["liquidity"] = {"usd": 90000.0}
pair_mcap_big["marketCap"] = 500000.0
listener.dx.best_pair = lambda ca: pair_mcap_big if ca.lower() == CA3 else None
_notes.clear()
s6 = run(listener.process_ca(CA3, SRC, None, "SCANNER"))
check("mcap > max → filtered:mcap", s6 == "filtered:mcap", s6)
check("mcap besar mengirim notif", len(_notes) == 1 and "mcap_terlalu_besar" in _notes[0],
      str(_notes))

# ---------------------------------------------------------------- restore
listener.dx.best_pair = _orig_best_pair
listener._notify = _orig_notify

print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:", FAIL)
    sys.exit(1)
