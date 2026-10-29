"""🧪 Uji AGE-GATE di FRONTEND radar tracker (v16.6 age-gate) — tanpa jaringan.

Lingkup:
  1. pool_too_old: pool umur 93 hari → (True, ~8091099)
  2. pool_too_old: pool umur 60s → (False, ~60)
  3-5. pool_too_old fail-open: None / {} / pairCreatedAt tak-valid
  6-9. should_notify_fast_skip: age_* senyap, tag lain tetap notify
  10. Ambang mengikuti setting goldilocks_max_age_sec (DB temporer)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db            # noqa: E402
from sniper.listener import should_notify_fast_skip  # noqa: E402
from sniper.tracker import pool_too_old  # noqa: E402

DAY_MS = 86_400_000


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = config.DB_PATH
    config.DB_PATH = tmp.name
    db.init()
    return tmp.name, old


def _torn_down(path, old):
    os.unlink(path)
    config.DB_PATH = old


def test_pool_93_days_too_old():
    now_ms = 1_800_000_000_000          # ms "sekarang" tetap (deterministik)
    created_ms = now_ms - 93 * DAY_MS   # 93 hari lalu
    pair = {"pairCreatedAt": created_ms}
    too_old, age = pool_too_old(pair, 300, now_ms=now_ms)
    assert too_old is True, (too_old, age)
    assert abs(age - 93 * 86_400) < 2, age
    print(f"PASS pool 93 hari → too_old (age={age:.0f}s)")


def test_pool_60s_ok():
    now_ms = 1_800_000_000_000
    pair = {"pairCreatedAt": now_ms - 60_000}
    too_old, age = pool_too_old(pair, 300, now_ms=now_ms)
    assert too_old is False, (too_old, age)
    assert 59 <= age <= 61, age
    print(f"PASS pool 60s → lolos (age={age:.1f}s)")


def test_fail_open_none():
    too_old, age = pool_too_old(None, 300)
    assert (too_old, age) == (False, None), (too_old, age)
    print("PASS pool_too_old(None) → fail-open (False, None)")


def test_fail_open_empty_dict():
    too_old, age = pool_too_old({}, 300)
    assert (too_old, age) == (False, None), (too_old, age)
    print("PASS pool_too_old({}) → fail-open (False, None)")


def test_fail_open_bad_created():
    too_old, age = pool_too_old({"pairCreatedAt": "x"}, 300)
    assert (too_old, age) == (False, None), (too_old, age)
    print("PASS pairCreatedAt='x' → fail-open (False, None)")


def test_silent_age_tags():
    # v16.8: age_* TIDAK lagi silent (user pilih tampilkan); tag tetap
    # harus benar-benar mulai dengan "age_" supaya filter tak salah sasaran.
    assert "age_8091099s".startswith("age_")
    assert "age_187s".startswith("age_")
    assert should_notify_fast_skip("age_8091099s") is True
    assert should_notify_fast_skip("age_187s") is True
    print("PASS notif age_* → NOTIFY (v16.8, anti-silent)")


def test_notify_other_tags():
    assert should_notify_fast_skip("liq_rendah") is True
    assert should_notify_fast_skip("mcap_out_of_range") is True
    print("PASS notif tag lain → tetap NOTIFY")


def test_threshold_follows_setting():
    path, old = _fresh_db()
    try:
        db.set_("goldilocks_max_age_sec", 120)
        assert db.get("goldilocks_max_age_sec", float) == 120.0
        # age gate hitung via _inspect_block memanggil db.get tanpa now_ms —
        # di sini uji ambang lewat fungsi murni dgn age relatif waktu nyata:
        now_ms = __import__("time").time() * 1000
        pair = {"pairCreatedAt": now_ms - 200_000}   # umur 200s
        too_old, age = pool_too_old(pair, db.get("goldilocks_max_age_sec", float))
        assert too_old is True, (too_old, age)
        assert 199 <= age <= 202, age
        # setting masih default di DB produksi: 300 (tidak diubah permanen)
        print(f"PASS setting 120s → pool 200s too_old=True (age={age:.0f}s)")
    finally:
        _torn_down(path, old)


if __name__ == "__main__":
    test_pool_93_days_too_old()
    test_pool_60s_ok()
    test_fail_open_none()
    test_fail_open_empty_dict()
    test_fail_open_bad_created()
    test_silent_age_tags()
    test_notify_other_tags()
    test_threshold_follows_setting()
    print("\nALL AGE-GATE-FRONTEND TESTS PASSED ✅")
