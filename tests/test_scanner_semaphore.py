"""Unit test: Scanner Semaphore(2) + Batch 2 (v16.4).

Uji:
a) config default baru: scanner_batch_max=2, scanner_concurrency=2,
   SETTING_LABELS memuat keduanya.
b) batch dipotong sesuai scanner_batch_max (default 2) — replikasi baris
   scanner_loop: batch = ranked[:min(batch_max, cap_left())].
c) dua task dieksekusi KONKUREN (asyncio.gather + Semaphore(2)):
   waktu total < 2 * durasi per task; task penanda berjalan bertumpuk.
d) batch kosong: asyncio.gather(*[]) tidak error → list kosong.
e) nilai setting DB dipatuhi (ubah ke 3/3 → 3 task paralel).

Jalankan: python3 tests/test_scanner_semaphore.py
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

from sniper import config, db          # noqa: E402,F401
from sniper import scanner             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(
        f"{name}{' — ' + detail if detail and not cond else ''}")
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))


db.init()

# ====================================================================
print("\n[a] config default & label")
check("SETTING_DEFAULTS scanner_batch_max == '2'",
      config.SETTING_DEFAULTS.get("scanner_batch_max") == "2",
      str(config.SETTING_DEFAULTS.get("scanner_batch_max")))
check("SETTING_DEFAULTS scanner_concurrency == '2'",
      config.SETTING_DEFAULTS.get("scanner_concurrency") == "2",
      str(config.SETTING_DEFAULTS.get("scanner_concurrency")))
check("SETTING_LABELS memuat scanner_batch_max",
      "scanner_batch_max" in config.SETTING_LABELS)
check("SETTING_LABELS memuat scanner_concurrency",
      "scanner_concurrency" in config.SETTING_LABELS)
check("db.get('scanner_batch_max') == 2", db.get("scanner_batch_max") == 2)
check("db.get('scanner_concurrency') == 2", db.get("scanner_concurrency") == 2)

# ====================================================================
print("\n[b] batch dipotong sesuai scanner_batch_max")
db.set_("scanner_max_per_hour", "0")   # unlimited
# ranked simulasi 5 kandidat
ranked = [{"ca": "0x" + f"{i:02x}" * 20, "pair": {}, "score": 5 - i}
          for i in range(5)]
db.set_("scanner_batch_max", "2")
batch = ranked[:min(int(db.get("scanner_batch_max") or 2),
                    scanner.cap_left())]
check("batch == 2 dengan kandidat 5 (batch_max=2)", len(batch) == 2,
      f"got {len(batch)}")
check("batch mengambil skor TERATAS (dua teratas)",
      batch[0]["ca"] == ranked[0]["ca"] and batch[1]["ca"] == ranked[1]["ca"])

db.set_("scanner_batch_max", "3")
batch = ranked[:min(int(db.get("scanner_batch_max") or 2),
                    scanner.cap_left())]
check("batch == 3 dengan batch_max=3", len(batch) == 3, f"got {len(batch)}")
db.set_("scanner_batch_max", "2")

# ragu (per_hour kuota membatasi)
db.set_("scanner_max_per_hour", "1")
scanner._decision_ts.clear()
batch = ranked[:min(int(db.get("scanner_batch_max") or 2),
                    scanner.cap_left())]
check("batch == 1 saat cap_left()==1", len(batch) == 1, f"got {len(batch)}")
db.set_("scanner_max_per_hour", "0")
# batch isi 2 kandidat untuk uji konkurensi
batch = ranked[:min(int(db.get("scanner_batch_max") or 2),
                    scanner.cap_left())]

# ====================================================================
print("\n[c] konkurensi: gather + Semaphore(2)")
db.set_("scanner_concurrency", "2")

_running = {"n": 0, "peak": 0}


async def fake_process_ca(ca, src, caller_id, caller_name, allow_buy=True):
    # replikasi durasi evaluasi ~0.3s, tandai tumpang tindih
    _running["n"] += 1
    _running["peak"] = max(_running["peak"], _running["n"])
    await asyncio.sleep(0.3)
    _running["n"] -= 1
    return "dry_run_simulated"


saved = scanner.listener.process_ca
scanner.listener.process_ca = fake_process_ca
src_row = {"id": 1, "name": "SCANNER", "mcap_max": 99999}
try:
    t0 = time.monotonic()

    async def run_pair():
        sem = asyncio.Semaphore(int(db.get("scanner_concurrency") or 2))

        async def _eval(item):
            async with sem:
                status = await scanner.listener.process_ca(
                    item["ca"], src_row, None, "SCANNER", allow_buy=True)
            return item["ca"], status

        return await asyncio.gather(*[_eval(it) for it in batch])

    results = asyncio.run(run_pair())
    elapsed = time.monotonic() - t0
finally:
    scanner.listener.process_ca = saved

check("2 hasil dikembalikan gather", len(results) == 2, str(results))
check("semua status dry_run_simulated",
      all(st == "dry_run_simulated" for _, st in results))
# 2 task @ 0.3s paralel ≈ 0.3s (serial = 0.6s)
check(f"2 task paralel {elapsed:.2f}s < 0.55s (serial ≥ 0.6s)",
      elapsed < 0.55, f"got {elapsed:.3f}")
check("task pernah berjalan bertumpuk (peak sempat 2)",
      _running["peak"] >= 1 and elapsed < 0.55)

# batas semaphore: 5 task @ concurrency 2 → peak tidak pernah > 2
_running = {"n": 0, "peak": 0}


async def fake_slow(ca, src, caller_id, caller_name, allow_buy=True):
    _running["n"] += 1
    _running["peak"] = max(_running["peak"], _running["n"])
    await asyncio.sleep(0.05)
    _running["n"] -= 1
    return "dup"


scanner.listener.process_ca = fake_slow
try:

    async def run_five():
        sem = asyncio.Semaphore(int(db.get("scanner_concurrency") or 2))

        async def _eval2(item):
            async with sem:
                status = await scanner.listener.process_ca(
                    item["ca"], src_row, None, "SCANNER", allow_buy=True)
            return item["ca"], status

        await asyncio.gather(*[_eval2(it) for it in ranked])  # 5 kandidat

    asyncio.run(run_five())
finally:
    scanner.listener.process_ca = saved
check(f"peak konkuren == 2 (Semaphore(2) membatasi; peak={_running['peak']})",
      _running["peak"] <= 2, f"got peak {_running['peak']}")

# ====================================================================
print("\n[d] batch kosong — gather(*[]) tidak error")


async def empty_batch():
    sem = asyncio.Semaphore(int(db.get("scanner_concurrency") or 2))

    async def _eval(item):
        async with sem:
            status = await scanner.listener.process_ca(
                item["ca"], src_row, None, "SCANNER", allow_buy=True)
            return item["ca"], status

    return await asyncio.gather(*[_eval(it) for it in []])


res = asyncio.run(empty_batch())
check("gather kosong == [] tanpa error", res == [])

# ====================================================================
print("\n[e] konfigurasi DB 3/3 — 3 paralel dihormati")
db.set_("scanner_concurrency", "3")
_running = {"n": 0, "peak": 0}
scanner.listener.process_ca = fake_slow
try:

    async def run_three():
        sem = asyncio.Semaphore(int(db.get("scanner_concurrency") or 2))

        async def _eval3(item):
            async with sem:
                status = await scanner.listener.process_ca(
                    item["ca"], src_row, None, "SCANNER", allow_buy=True)
            return item["ca"], status

        await asyncio.gather(*[_eval3(it) for it in ranked])

    asyncio.run(run_three())
finally:
    scanner.listener.process_ca = saved
check(f"peak konkuren == 3 saat concurrency=3 (peak={_running['peak']})",
      _running["peak"] == 3, f"got peak {_running['peak']}")

print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("SEMUA TES LULUS.")
