"""Unit test scanner hour cap: unlimited saat 0 + kuota hanya buat BUY.

Cakupan:
  a) cap_left() == 999999 saat scanner_max_per_hour = "0" (UNLIMITED)
  b) status "filtered:safety" TIDAK menambah _decision_ts (skip gratis)
  c) status "dry_run_simulated" / "bought" menambah _decision_ts (makan kuota)
  d) angka > 0 tetap berfungsi sebagai kuota BUY per jam (default nilai dari
     config.SETTING_DEFAULTS adalah "0")
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# DB_PATH harus diset sebelum import sniper.config (dibaca saat import)
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["DB_PATH"] = _tmpdb.name

from sniper import config, db   # noqa: E402,F401
from sniper import scanner      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(
        f"{name}{' — ' + detail if detail and not cond else ''}")
    print(f"  {'OK' if cond else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not cond else ""))


db.init()


def reset_cap(value):
    db.set_("scanner_max_per_hour", str(value))
    scanner._decision_ts.clear()


# ======================================================================
# a) unlimited saat "0"
# ======================================================================
print("\n=== a. cap_left(): 0 = UNLIMITED ===")
reset_cap(0)
first_left = scanner.cap_left()
check("cap_left()==999999 saat max_per_hour='0'",
      first_left == 999999, f"got {first_left}")
scanner._note_decision()
scanner._note_decision()
second_left = scanner.cap_left()
check("cap_left() tetap 999999 walau 2 BUY tercatat (batas non-aktif)",
      second_left == 999999, f"got {second_left}")
scanner._decision_ts.clear()

# ======================================================================
# b) skip (filtered:safety) tidak makan kuota
# ======================================================================
print("\n=== b. filtered:safety TIDAK menambah _decision_ts ===")
reset_cap(5)


def apply_status(status):
    """Replikasi logika scanner_loop() yang baru."""
    if status in ("bought", "dry_run_simulated"):
        scanner._note_decision()


for st in ("filtered:safety", "skip_agent", "no_liquidity", "dup"):
    apply_status(st)
check("4 skip/eval bukan buy → _decision_ts tetap kosong",
      len(scanner._decision_ts) == 0, str(scanner._decision_ts))
check("cap_left() tidak berkurang oleh skip",
      scanner.cap_left() == 5, f"got {scanner.cap_left()}")

# ======================================================================
# c) BUY (dry_run_simulated / bought) makan kuota
# ======================================================================
print("\n=== c. dry_run_simulated / bought menambah _decision_ts ===")
apply_status("dry_run_simulated")
check("dry_run_simulated → _decision_ts nambah 1",
      len(scanner._decision_ts) == 1, str(scanner._decision_ts))
check("cap_left() turun ke 4", scanner.cap_left() == 4,
      f"got {scanner.cap_left()}")
apply_status("bought")
check("bought → _decision_ts nambah jadi 2",
      len(scanner._decision_ts) == 2, str(scanner._decision_ts))
check("cap_left() turun ke 3", scanner.cap_left() == 3,
      f"got {scanner.cap_left()}")

# ======================================================================
# d) kuota >0 benar-benar membatasi BUY + rotasi waktu 1 jam
# ======================================================================
print("\n=== d. kuota BUY terbatas (angka > 0) + rotasi waktu ===")
reset_cap(2)
for _ in range(2):
    apply_status("bought")
apply_status("dry_run_simulated")  # BUY ke-3 tetap tercatat
check("cap_left()==0 setelah 2 BUY dengan kuota 2",
      scanner.cap_left() == 0, f"got {scanner.cap_left()}")
ts_before = list(scanner._decision_ts)
apply_status("filtered:safety")
check("skip setelah kuota habis tidak mengubah _decision_ts",
      list(scanner._decision_ts) == ts_before)

# rotasi waktu: ts > 1 jam dibersihkan
reset_cap(2)
scanner._note_decision(now=time.time() - 7200)
check("ts 2 jam lalu dibersihkan → cap_left kembali penuh",
      scanner.cap_left() == 2, f"got {scanner.cap_left()}")
check("_decision_ts dikosongkan oleh cleanup",
      len(scanner._decision_ts) == 0, str(scanner._decision_ts))

# default config
check("config default scanner_max_per_hour == '0'",
      config.SETTING_DEFAULTS.get("scanner_max_per_hour") == "0",
      str(config.SETTING_DEFAULTS.get("scanner_max_per_hour")))

print()
print(f"TOTAL: {len(PASS)} OK, {len(FAIL)} FAIL")
if FAIL:
    print(" FAIL:")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
