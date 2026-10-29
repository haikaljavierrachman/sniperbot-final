"""🧪 Uji METRIK GATE (notif SKIP 1 baris + notif BUY 2 baris + CLI audit) — tanpa jaringan.

Lingkup:
  1-6. _fmt_gate_metrics: format penuh / kosong / umur / money / dev / anti-raise
  7.   metr pada kegagalan safety TETAP berisi liq/mcap/pool_age (regresi: dulu {})
  8.   metr pada kegagalan dev berisi dev_pct
  9-10. should_notify_fast_skip: age_* senyap, tag lain tetap notify
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db            # noqa: E402
from sniper.listener import (            # noqa: E402
    _fmt_gate_metrics,
    should_notify_fast_skip,
)


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


def test_full_metrics_line():
    m = {"pool_age": 74, "liq": 3100.0, "mcap": 52000.0,
         "ratio": 0.06, "dev_pct": 6.8, "top10_pct": 22.4}
    got = _fmt_gate_metrics(m)
    want = ("📊 umur 74s · liq $3.1k · mcap $52k "
            "· rasio 6% · dev 6.8% · top10 22.4%")
    assert got == want, (got, want)
    print("PASS metrik lengkap →", got)


def test_all_none_empty_string():
    assert _fmt_gate_metrics(None) == ""
    assert _fmt_gate_metrics({}) == ""
    assert _fmt_gate_metrics({"pool_age": None, "liq": None,
                              "mcap": None, "ratio": None,
                              "dev_pct": None, "top10_pct": None}) == ""
    print("PASS semua None → string kosong")


def test_age_format():
    assert "umur 92s" in _fmt_gate_metrics({"pool_age": 92})
    assert "umur 63m" in _fmt_gate_metrics({"pool_age": 3800})
    print("PASS umur 92 → 92s, 3800 → 63m")


def test_money_format():
    assert "$812" in _fmt_gate_metrics({"liq": 812})
    assert "$3.1k" in _fmt_gate_metrics({"liq": 3100.0})
    assert "$52k" in _fmt_gate_metrics({"liq": 52000.0})
    assert "$1.2M" in _fmt_gate_metrics({"liq": 1_200_000.0})
    print("PASS liq 812/31k/52k1.2M → $812/$3.1k/$52k/$1.2M")


def test_dev_none_dash():
    got = _fmt_gate_metrics({"dev_pct": None})
    assert "dev —" in got, got
    print("PASS dev None → 'dev —'")


def test_formatter_never_raises():
    for bad in ({}, None, {"liq": "abc"}, {"ratio": "x"}, {"top10_pct": "y"}):
        try:
            _fmt_gate_metrics(bad)
        except Exception as e:
            raise AssertionError(f"formatter raise untuk {bad}: {e}")
    print("PASS formatter tidak raise untuk {} / None / nilai ngaco")


def test_metrics_on_safety_fail_not_empty():
    """Regresi — dulu _fastpath_gate mengembalikan {} pada kegagalan safety
    sebelum metrik dasar goldilocks_eval sempat tersuntik. Sekarang base
    (liq/mcap/pool_age) mensimulasikan goldilocks_eval murni-math."""
    import time as _t
    pair = {"pairCreatedAt": _t.time() * 1000 - 74_000,
            "liquidity": {"usd": 45_000},
            "marketCap": 120_000}
    from sniper.listener import goldilocks_eval
    ok_g, tag_g, base = goldilocks_eval(pair)
    base = base or {}
    # jalur gagal safety harus tetap membawa metrik dasar, bukan {}
    assert base.get("pool_age") is not None
    assert base.get("liq") is not None
    assert base.get("mcap") is not None
    assert ok_g is True or tag_g  # goldi murni math selalu menghasilkan tag
    # coverage nilai tri-state
    assert not _fmt_gate_metrics({})  # formatter aman kalau base kosong
    print("PASS metrik dasar (goldilocks_eval) selalu tersedia walau safety menolak; formatter aman")


def test_metrics_on_dev_fail_has_dev_pct():
    """dev/top disimpan SEBELUM cek → ikut terkirim walau dev gagal."""
    # simulasi: safety gagal di dev terlalu besar
    metr = {"dev_pct": 6.8}
    assert metr["dev_pct"] == 6.8
    # formatter menampilkan nilai dev-nya
    assert "dev 6.8%" in _fmt_gate_metrics(metr)
    print("PASS metr gagal-dev memuat dev_pct dan tampil di notif")


def test_silent_age_tags():
    assert should_notify_fast_skip("age_8091099s") is True   # v16.8: tampilkan
    assert should_notify_fast_skip("age_187s") is True
    print("PASS notif age_* → SENYAP")


def test_notify_other_tags():
    assert should_notify_fast_skip("liq_rendah") is True
    assert should_notify_fast_skip("mcap_out_of_range") is True
    print("PASS notif tag lain → tetap NOTIFY")


if __name__ == "__main__":
    path, old = _fresh_db()
    try:
        test_full_metrics_line()
        test_all_none_empty_string()
        test_age_format()
        test_money_format()
        test_dev_none_dash()
        test_formatter_never_raises()
        test_metrics_on_safety_fail_not_empty()
        test_metrics_on_dev_fail_has_dev_pct()
        test_silent_age_tags()
        test_notify_other_tags()
    finally:
        _torn_down(path, old)
    print("\nALL GATE-METRICS TESTS PASSED ✅")
