"""Level 1 — pool_type() & liq_usd_or_none() dengan label Dexscreener."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["DB_PATH"] = _tmpdb.name

from sniper import db          # noqa: E402
from sniper import dexscreener as dx   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(
        f"{name}{' — ' + detail if detail and not cond else ''}")
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if not cond else ""))


db.init()

# ============================================================
# 1. pool_type(): v4 dominant (70% pool sebenarnya v4)
# ============================================================
print("\n=== 1. pool_type() ===")
check("v4 label → 'v4'",
      dx.pool_type({"labels": ["v4"]}) == "v4",
      dx.pool_type({"labels": ["v4"]}))
check("label 'v4' huruf campur → 'v4'",
      dx.pool_type({"labels": ["V4"]}) == "v4",
      dx.pool_type({"labels": ["V4"]}))
check("v2 label → 'v2'", dx.pool_type({"labels": ["v2"]}) == "v2")
check("v3 label → 'v3'di fallback", dx.pool_type({"labels": ["v3", "uniswap"]}) == "v3")
check("labels kosong → 'v3' (fallback)", dx.pool_type({}) == "v3")
check("labels None → 'v3'", dx.pool_type({"labels": None}) == "v3")
check("labels v2 + v4 → 'v4' (v4 dicek DULUAN)",
      dx.pool_type({"labels": ["v2", "v4"]}) == "v4",
      dx.pool_type({"labels": ["v2", "v4"]}))
check("labels tak dikenal ('top_pools') → v3",
      dx.pool_type({"labels": ["top_pools"]}) == "v3")

# ============================================================
# 2. liq_usd_or_none(): None bila liquidity null/vakum
# ============================================================
print("\n=== 2. liq_usd_or_none() ===")
check("liquidity null → None",
      dx.liq_usd_or_none({"liquidity": None}) is None)
check("liquidity field hilang → None",
      dx.liq_usd_or_none({}) is None)
check("liquidity.usd null → None",
      dx.liq_usd_or_none({"liquidity": {"usd": None}}) is None)
check("liquidity.usd 0 → 0 (bukan None)",
      dx.liq_usd_or_none({"liquidity": {"usd": 0}}) == 0.0)
check("liquidity.usd 123.4 → 123.4",
      dx.liq_usd_or_none({"liquidity": {"usd": 123.4}}) == 123.4)

# ============================================================
# 3. liq_usd() LAMA TIDAK BERUBAH perilakunya (null tetap 0, bukan error)
# ============================================================
print("\n=== 3. liq_usd() (lama) ===")
check("null → 0 (perilaku sama dulu)", dx.liq_usd({"liquidity": None}) == 0.0)
check("122.5 → 122.5", dx.liq_usd({"liquidity": {"usd": 122.5}}) == 122.5)

print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("SEMUA LULUS ✅")
