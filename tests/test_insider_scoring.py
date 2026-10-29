"""🧪 Uji skema bobot insider 2 sumber (v16.9) — tanpa jaringan.

Premis: Blockscout (BS) dan GeckoTerminal (GT) BUKAN saksi independen —
GT menurunkan data dari chain yang sama. Bobot harus mencerminkan
kualitas bukti, bukan jumlah sumber yang kebetulan menyebut alamat sama.

Lingkup:
  1. BS + GT            → 2,0 per koin
  2. BS saja, ≥2 beli   → 1,0
  3. BS saja, 1 beli    → 0,5
  4. GT saja            → 0,25 + karantina (enabled=0)
  5. kontrak & burn     → dibuang total
  6. GT error ≠ GT kosong (jangan turunkan bobot diam-diam)
  7. cap 2,0 per koin + decay waktu
  8. gate butuh ≥2 CA terverifikasi Blockscout
  9. persist: promoted vs quarantine, last_verified
"""
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "screen_insiders", ROOT / "tools" / "screen_insiders.py")
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)

CA1 = "0x" + "11" * 20
CA2 = "0x" + "22" * 20
CA3 = "0x" + "33" * 20

GOOD = "0x" + "a1" * 16 + "00000001"
BURN = "0x000000000000000000000000000000000000dead"
CONTRACT = "0x" + "c0" * 20


def _bs(addr, buys=1, contract=False):
    return [{"to": addr, "value": 1.0, "ts": int(time.time()),
             "is_contract": contract} for _ in range(buys)]


def _run(fake_bs, fake_gt, cas, ca_ts=None, bs_state="ok"):
    si.fetch_early_buyers_ex = lambda ca: (bs_state, fake_bs.get(ca, []))
    si.fetch_gecko_buyers_ex = lambda ca: fake_gt.get(ca, ("ok", set()))
    return si.cross_check(cas, ca_ts=ca_ts)


def _row(rows, addr):
    for r in rows:
        if r["address"] == addr:
            return r
    raise AssertionError(f"{addr} tidak ada di hasil: {rows}")


def test_both_sources():
    rows = _run({CA1: _bs(GOOD), CA2: _bs(GOOD)},
                {CA1: ("ok", {GOOD}), CA2: ("ok", {GOOD})}, [CA1, CA2])
    r = _row(rows, GOOD)
    assert r["score"] == 4.0, r          # 2 koin × 2,0
    assert r["qualifies"] is True, r
    assert len(r["verified_cas"]) == 2, r
    print("PASS BS+GT = 2,0/koin → 2 koin = 4,0 dan lolos gate")


def test_bs_only_strong_and_weak():
    strong = "0x" + "a1" * 16 + "00000002"
    weak = "0x" + "a1" * 16 + "00000003"
    rows = _run({CA1: _bs(strong, 2) + _bs(weak, 1),
                 CA2: _bs(strong, 3) + _bs(weak, 1)}, {}, [CA1, CA2])
    assert _row(rows, strong)["score"] == 2.0, _row(rows, strong)
    assert _row(rows, weak)["score"] == 1.0, _row(rows, weak)
    assert _row(rows, strong)["qualifies"] is True
    assert _row(rows, weak)["qualifies"] is False
    print("PASS BS saja: akumulasi (≥2 beli) 1,0 vs numpang lewat 0,5 per koin")


def test_gt_only_quarantine():
    rows = _run({}, {CA1: ("ok", {GOOD}), CA2: ("ok", {GOOD})}, [CA1, CA2])
    r = _row(rows, GOOD)
    assert r["score"] == 0.5, r          # 2 × 0,25
    assert r["qualifies"] is False, r
    assert len(r["gt_only_cas"]) == 2, r
    assert r["needs_retry"] is True, r
    print("PASS GT-only = 0,25/koin, tidak pernah lolos gate, masuk karantina")


def test_contract_and_burn_rejected():
    rows = _run({CA1: _bs(CONTRACT, 5, contract=True) + _bs(BURN, 2),
                 CA2: _bs(CONTRACT, 5, contract=True)}, {}, [CA1, CA2])
    addrs = {r["address"] for r in rows}
    assert CONTRACT not in addrs, addrs
    assert BURN not in addrs, addrs
    assert rows == [], rows
    print("PASS kontrak router/pool & alamat burn dibuang total")


def test_gt_error_not_same_as_empty():
    rows_err = _run({CA1: _bs(GOOD, 1), CA2: _bs(GOOD, 1)},
                    {CA1: ("error", set()), CA2: ("error", set())}, [CA1, CA2])
    r = _row(rows_err, GOOD)
    assert r["needs_retry"] is True, r
    assert "|bs?" in r["sources"], r     # ditandai tidak terjawab, bukan "kosong"
    rows_empty = _run({CA1: _bs(GOOD, 1), CA2: _bs(GOOD, 1)},
                      {CA1: ("ok", set()), CA2: ("ok", set())}, [CA1, CA2])
    r2 = _row(rows_empty, GOOD)
    assert r2["needs_retry"] is False, r2
    assert "|bs?" not in r2["sources"], r2
    print("PASS GT error ditandai 'bs?' + needs_retry, bukan dianggap 'tidak ada'")


def test_cap_and_decay():
    old = time.time() - 10 * 86400
    rows = _run({CA1: _bs(GOOD, 9)}, {CA1: ("ok", {GOOD})},
                [CA1], ca_ts={CA1: old})
    r = _row(rows, GOOD)
    assert r["score"] == 0.8, r          # 2,0 × 0,4 (decay >7 hari), cap tak lewat
    rows_fresh = _run({CA1: _bs(GOOD, 9)}, {CA1: ("ok", {GOOD})}, [CA1])
    assert _row(rows_fresh, GOOD)["score"] == 2.0
    print("PASS cap 2,0/koin + decay 0,4 untuk koin berumur 10 hari")


def test_gate_needs_two_verified_ca():
    rows = _run({CA1: _bs(GOOD, 3)}, {CA1: ("ok", {GOOD})}, [CA1])
    r = _row(rows, GOOD)
    assert r["score"] == 2.0, r
    assert r["qualifies"] is False, r    # 1 koin saja tidak cukup
    print("PASS skor 2,0 dari 1 koin saja TIDAK lolos (butuh ≥2 CA)")


def test_persist_promoted_vs_quarantine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = config.DB_PATH
    config.DB_PATH = tmp.name
    try:
        db.init()
        promoted = "0x" + "a1" * 16 + "00000010"
        quarantined = "0x" + "a1" * 16 + "00000011"
        rows = [
            {"address": promoted, "score": 4.0, "n_ca": 2,
             "verified_cas": [CA1, CA2], "gt_only_cas": [],
             "sources": f"{CA1}|bs+gt,{CA2}|bs+gt",
             "qualifies": True, "needs_retry": False},
            {"address": quarantined, "score": 0.5, "n_ca": 2,
             "verified_cas": [], "gt_only_cas": [CA1, CA3],
             "sources": f"{CA1}|gt,{CA3}|gt",
             "qualifies": False, "needs_retry": True},
        ]
        si.persist(rows)
        saved = {r["address"]: r for r in db.list_insiders()}
        assert saved[promoted]["enabled"] == 1, saved[promoted]
        assert saved[promoted]["score"] == 4.0, saved[promoted]
        assert saved[promoted]["last_verified"] > 0, saved[promoted]
        assert saved[quarantined]["enabled"] == 0, saved[quarantined]
        assert saved[quarantined]["label"] == "quarantine_gt", saved[quarantined]
        assert saved[quarantined]["last_verified"] == 0, saved[quarantined]
        # karantina tidak boleh ikut dipantau tracker
        active = db.get_active_insiders()
        assert promoted in active and quarantined not in active, active
        print("PASS persist: promoted enabled=1, GT-only enabled=0 + tidak dipantau")
    finally:
        config.DB_PATH = old
        os.unlink(tmp.name)


def test_bs_error_skips_ca():
    """Outage Blockscout tidak boleh memalsukan baris GT-only/karantina."""
    rows = _run({}, {CA1: ("ok", {GOOD}), CA2: ("ok", {GOOD})},
                [CA1, CA2], bs_state="error")
    assert rows == [], rows
    print("PASS Blockscout error → CA dilewati, bukan karantina massal GT-only")


if __name__ == "__main__":
    test_both_sources()
    test_bs_only_strong_and_weak()
    test_gt_only_quarantine()
    test_contract_and_burn_rejected()
    test_gt_error_not_same_as_empty()
    test_cap_and_decay()
    test_gate_needs_two_verified_ca()
    test_persist_promoted_vs_quarantine()
    test_bs_error_skips_ca()
    print("\n✅ SEMUA TES SKEMA BOBOT 2 SUMBER LULUS")
