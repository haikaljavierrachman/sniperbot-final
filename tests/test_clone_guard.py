"""🧪 Uji CLONE/OPERATOR-FAMILY GUARD v16.6 — tanpa jaringan sungguhan.

Lingkup:
  1. creator_of (sniper.safety): jalur creation_transaction_hash →
     /transactions/{tx}.from, dengan cache deployer_registry
     (monkeypatch _get — Blockscout TIDAK dipanggil asli).
  2. template_fp + funders_of (sniper.clone_guard, monkeypatch _bs_get).
  3. is_clone_risk: template sibling failed + funder family failed.
  4. mark_family_failed: status 'failed' menyebar ke CA se family & funder.
  5. GeckoTerminal rate limiter: max 25 req/menit + jeda min 2.5s.
"""
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db            # noqa: E402
from sniper import safety, clone_guard   # noqa: E402
from sniper import geckoterminal as gt   # noqa: E402

MARVIN1 = "0x0da7a5b380ee405753d6690b2e087d615eb57777"
MARVIN2 = "0xa1e542496fa41c63c0bd745b54f812346c3f7777"
ROUTER = "0x1cbaf24d53fe930fce8eff149fa797d2611da149"   # launcher Pons — BUKAN deployer
DEPLOYER1 = "0x2e4aaa4582550e0ebad166624b211c0b23919f33"
DEPLOYER2 = "0xb4ee04d5a7b37f1cb14f8e14fcc5029c8df95d49"
FUNDER = "0x22820fdaf2ebaeb90f469996a00f0e22316e0571"
MARVIN3 = "0x3a0d7bf648ec98e9ae45879e6581691887147777"


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = config.DB_PATH
    config.DB_PATH = tmp.name
    db.init()
    return tmp.name, old


# =========================================================
# 1. creator_of — jalur creation_tx, BUKAN creator_address_hash
# =========================================================

def test_creator_of_uses_creation_tx():
    """creator_address_hash (router) HARUS diabaikan; deployer asli dari
    creation_transaction_hash. Hasil di-cache → call kedua tak meng-hits net."""
    path, old = _fresh_db()
    calls = []
    fake = {
        f"/addresses/{MARVIN1}": {"creator_address_hash": ROUTER,   # jebakan lama
                                  "creation_transaction_hash": "0x44tx"},
        "/transactions/0x44tx": {"from": {"hash": DEPLOYER1}},
    }
    def fake_get(p, params=None):
        calls.append(p)
        return fake.get(p, {})
    old_get = safety._get
    try:
        safety._get = fake_get
        dev = safety.creator_of(MARVIN1)
        assert dev == DEPLOYER1, dev
        assert all("/transactions/" in c or "/addresses/" in c for c in calls)
        n_calls_first = len(calls)
        # kedua kali: dari cache, tanpa panggilan baru
        dev2 = safety.creator_of(MARVIN1)
        assert dev2 == DEPLOYER1, dev2
        assert len(calls) == n_calls_first, (n_calls_first, calls)
        row = clone_guard.get_registry(MARVIN1)
        assert row and row["deployer"] == DEPLOYER1, row
        print("PASS creator_of creation-tx path + cache")
    finally:
        safety._get = old_get
        os.unlink(path)
        config.DB_PATH = old


def test_creator_of_not_router():
    """Hasil TIDAK PERNAH = router launcher (0x1cbaF24D…)."""
    path, old = _fresh_db()
    fake = {
        f"/addresses/{MARVIN2}": {"creator_address_hash": ROUTER,
                                  "creation_transaction_hash": "0xcab9"},
        "/transactions/0xcab9": {"from": {"hash": DEPLOYER2.upper()}},
    }
    old_get = safety._get
    try:
        safety._get = lambda p, params=None: fake.get(p, {})
        dev = safety.creator_of(MARVIN2)
        assert dev == DEPLOYER2, dev
        assert dev != ROUTER
        # dev_pct punya deployer asli: dev != router
        print("PASS creator_of deployer asli != router")
    finally:
        safety._get = old_get
        os.unlink(path)
        config.DB_PATH = old


# =========================================================
# 2. template_fp — sha256(deployed_bytecode[:1000])
# =========================================================

def test_template_fp():
    path, old = _fresh_db()
    bytecode = "0x60" + "ab" * 499
    expect = hashlib.sha256(bytecode[:1000].encode()).hexdigest()
    fake = {f"/smart-contracts/{MARVIN1}": {"deployed_bytecode": bytecode}}
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: fake.get(p, None)
        got = clone_guard.template_fp(MARVIN1)
        assert got == expect, (got, expect)
        # endpoint gagal → None
        assert clone_guard.template_fp("0x" + "f" * 40) is None
        print("PASS template_fp sha256(deployed_bytecode[:1000])")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


def test_funders_of():
    path, old = _fresh_db()
    fake = {
        f"/addresses/{DEPLOYER1}/transactions": {"items": [
            {"from": {"hash": FUNDER}, "to": {"hash": DEPLOYER1}, "value": "10000000000000000"},
            {"from": {"hash": DEPLOYER1}, "to": {"hash": "0x" + "9" * 40}, "value": "5"},
            {"from": {"hash": "0x1111111111111111111111111111111111111111"},
             "to": {"hash": DEPLOYER1}, "value": "0"},   # value 0 → skip
            {"from": {"hash": "0x2222222222222222222222222222222222222222"},
             "to": {"hash": "0x" + "9" * 40}, "value": "5"},  # bukan deposit → skip
        ]},
    }
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: fake.get(p, None)
        funders = clone_guard.funders_of(DEPLOYER1)
        assert funders == [FUNDER], funders
        print("PASS funders_of ETH-in-from-other-EOA")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


# =========================================================
# 3. is_clone_risk — template sibling failed & funder family
# =========================================================

def _register(ca, deployer, fp, sym="Marvin", name="PonsLauncherToken"):
    with db._lock, db._conn() as c:
        clone_guard._init_tables(c)
        c.execute("""INSERT OR REPLACE INTO deployer_registry
            (ca,deployer,template_fp,symbol,name,total_supply,funders,status,reason,ts)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (ca, deployer, fp, sym, name, "1000", "", "ok", "", int(time.time())))


def test_is_clone_risk_template_sibling():
    path, old = _fresh_db()
    fp = "abc123"
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: None   # tanpa jaringan
        # saudara template yang GAGAL (Marvin #1)
        _register(MARVIN1, DEPLOYER1, fp)
        with db._lock, db._conn() as c:
            clone_guard._init_tables(c)
            c.execute("UPDATE deployer_registry SET status='failed', reason='rug' WHERE ca=?",
                      (MARVIN1,))
        # CA baru dengan fp/symbol/name sama → TOLAK
        _register(MARVIN3, "0x" + "3" * 40, fp)
        risk, reason = clone_guard.is_clone_risk(MARVIN3)
        assert risk, reason
        assert "template clone" in reason, reason
        print("PASS is_clone_risk template-sibling-failed → TOLAK")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


def test_is_clone_risk_funder_family():
    """Funder yang sama mendanai 2 deployer (satu executor gagal) → TOLAK."""
    path, old = _fresh_db()
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: None
        # Marvin #1: sudah ditandai gagal bersama funder-nya
        _register(MARVIN1, DEPLOYER1, "fp1")
        with db._lock, db._conn() as c:
            clone_guard._init_tables(c)
            c.execute("UPDATE deployer_registry SET status='failed' WHERE ca=?",
                      (MARVIN1,))
            c.execute("""INSERT OR REPLACE INTO operator_funders
                (funder,deployer,ca,status,ts) VALUES(?,?,?,?,?)""",
                (FUNDER, DEPLOYER1, MARVIN1, "failed", int(time.time())))
        # Marvin #2: deployer BERBEDA, tapi didanai funder yang sama
        # (tanpa template sama — testing jalur funder saja)
        _register(MARVIN2, DEPLOYER2, "fp2")
        with db._lock, db._conn() as c:
            c.execute("""INSERT OR REPLACE INTO operator_funders
                (funder,deployer,ca,status,ts) VALUES(?,?,?,?,?)""",
                (FUNDER, DEPLOYER2, MARVIN2, "ok", int(time.time())))
        # jalur funder: deployer Marvin2 didanai FUNDER yang statusnya failed
        # (dirujuk lewat join d.ca=ca dan f.deployer)
        risk, reason = clone_guard.is_clone_risk(MARVIN2, fresh=False)
        assert risk, (risk, reason)
        assert "funder" in reason, reason
        print("PASS is_clone_risk funder-family-failed → TOLAK")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


def test_is_clone_risk_fail_open():
    """Blockscout tidak terjangkau → (False, …) JANGAN blokir."""
    path, old = _fresh_db()
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: None
        risk, reason = clone_guard.is_clone_risk(MARVIN1)
        assert risk is False, (risk, reason)
        print("PASS is_clone_risk fail-open (Blockscout down → lanjut)")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


def test_mark_family_failed():
    path, old = _fresh_db()
    old_bs = clone_guard._bs_get
    try:
        clone_guard._bs_get = lambda p, timeout=8: None
        fp, sym, nm = "fpX", "Marvin", "PonsLauncherToken"
        _register(MARVIN1, DEPLOYER1, fp, sym, nm)
        _register(MARVIN2, DEPLOYER2, fp, sym, nm)
        OTHER = "0x" + "e" * 40
        _register(OTHER, "0x" + "7" * 40, "fpy", "Bye", "OtherToken")
        with db._lock, db._conn() as c:
            c.execute("""INSERT OR REPLACE INTO operator_funders
                (funder,deployer,ca,status,ts) VALUES(?,?,?,?,?)""",
                (FUNDER, DEPLOYER1, MARVIN1, "ok", int(time.time())))
        clone_guard.mark_family_failed(MARVIN1, "rug pnl -95%")
        rows = {r["ca"]: r for r in [
            dict(r) for r in (lambda conn: conn.execute(
                "SELECT * FROM deployer_registry").fetchall())(db._conn())]}
        assert rows[MARVIN1]["status"] == "failed"
        assert rows[MARVIN2]["status"] == "failed"      # se family template
        assert rows[OTHER]["status"] == "ok"            # bukan keluarga
        with db._lock, db._conn() as c:
            f = c.execute("SELECT status FROM operator_funders WHERE funder=?",
                          (FUNDER,)).fetchone()
        assert f["status"] == "failed", dict(f)
        print("PASS mark_family_failed menyebar ke seluruh family + funder")
    finally:
        clone_guard._bs_get = old_bs
        os.unlink(path)
        config.DB_PATH = old


# =========================================================
# 4. GeckoTerminal rate limiter (tanpa jaringan)
# =========================================================

def test_gecko_rate_limiter():
    """Jeda min 2.5s antar request; sliding window membatasi burst."""
    gt._rl_window.clear()
    gt._rl_last = 0.0
    sleeps = []
    orig_sleep = gt.time.sleep
    gt.time.sleep = lambda s: sleeps.append(s)
    fake_ts = {"t": time.monotonic()}
    orig_mono = gt.time.monotonic
    class FakeMono:
        pass
    gt.time.monotonic = lambda: fake_ts["t"]
    try:
        # 4 request BURST tanpa jeda nyata → request 2,3,4 wajib menunggu 2.5s
        for i in range(4):
            gt._throttle()
        waits = [s for s in sleeps if s >= 2.0]
        assert len(waits) >= 2, sleeps   # request 2..4 minimal sebagian menunggu
        # sliding window: 25 request dalam 1 menit → ke-26 menunggu
        sleeps.clear()
        gt._rl_window.clear()
        fake_ts["t"] += 70   # window bebas lama
        tanya = fake_ts["t"]
        for _ in range(25):
            gt._throttle()
        before = len(sleeps)
        gt._throttle()       # request ke-26 → harus menunggu (window penuh)
        assert len(sleeps) > before, sleeps
        print("PASS geckoterminal rate limiter (2.5s min gap + 25/min window)")
    finally:
        gt.time.sleep = orig_sleep
        gt.time.monotonic = orig_mono
        # WAJIB: bersihkan state rate-limiter supaya test berikutnya tidak
        # mewarisi timestamp jam palsu (menyebabkan sleep ~70s nyata).
        gt._rl_window.clear()
        gt._rl_last = 0.0


def test_gecko_norm_trade():
    """Normalisasi respons trades GeckoTerminal (contoh nyata Marvin pool)."""
    item = {"attributes": {
        "block_number": 97439142,
        "block_timestamp": "2026-10-25T08:08:34Z",
        "tx_hash": "0x2457db5f898789a88171ea7bc53220ed1365a331a0b0a876f733a8a2dd6481f8",
        "tx_from_address": "0x3dd1eb2b6b9247b649a5dba7575f3f0078059e6b",
        "kind": "buy",
        "from_token_amount": "0.000427499999999999",
        "to_token_amount": "16254.4959131883",
        "price_to_in_usd": "0.0000616281861553903",
        "price_from_in_usd": "2343.24",
        "volume_in_usd": "1.0017351",
    }}
    n = gt._norm_trade(item)
    assert n["side"] == "buy"
    assert n["wallet"] == "0x3dd1eb2b6b9247b649a5dba7575f3f0078059e6b"
    assert n["tx_hash"].startswith("0x2457")
    assert abs(n["token_amount"] - 16254.4959131883) < 1e-6
    assert abs(n["price_usd"] - 0.0000616281861553903) < 1e-12
    assert abs(n["usd"] - 1.0017351) < 1e-6
    assert n["block"] == 97439142
    assert n["ts"] > 0
    # sell → token_amount dari from_token_amount
    item2 = dict(attributes=dict(item["attributes"], kind="sell",
                                 from_token_amount="99", price_from_in_usd="0.5"))
    n2 = gt._norm_trade(item2)
    assert n2["side"] == "sell" and n2["token_amount"] == 99.0 and n2["price_usd"] == 0.5
    # kind aneh → None
    assert gt._norm_trade({"attributes": {"kind": "weird", "tx_from_address": "0x" + "a" * 40}}) is None
    print("PASS geckoterminal _norm_trade (Marvin sample)")


def test_gecko_failure_returns_empty():
    """Semua kegagalan → list kosong, tidak raise."""
    assert gt.pools_of("0x" + "z" * 39) == []   # CA invalid → fetch gagal / kosong
    assert gt.trades("0xdead") == []
    assert gt.trades("") == []
    print("PASS geckoterminal fail → [] (tanpa raise)")


# =========================================================
# 5. integrasi screen_insiders: skor dual-source
# =========================================================

def test_screen_insiders_dual_source_scoring():
    import tools.screen_insiders as si
    W1 = "0x" + "a" * 40
    W2 = "0x" + "b" * 40
    W3 = "0x" + "e" * 40
    C1 = "0x" + "c" * 40
    C2 = "0x" + "d" * 40
    old_feb = si.fetch_early_buyers_ex
    old_fge = si.fetch_gecko_buyers_ex

    def _t(*addrs):
        return [{"to": a, "value": 1.0, "ts": 0, "is_contract": False}
                for a in addrs]

    try:
        # W1 di KEDUA sumber (2,0/koin), W2 hanya Blockscout 1 beli (0,5/koin)
        si.fetch_early_buyers_ex = lambda ca, max_buyers=50, retries=2: ("ok", _t(W1, W2))
        si.fetch_gecko_buyers_ex = lambda ca, max_pools=3: ("ok", {W1})
        rows = si.cross_check([C1, C2])
        by = {r["address"]: r for r in rows}
        assert W1 in by and W2 in by, rows
        assert by[W1]["score"] == 4.0, by[W1]      # 2 koin × 2,0
        assert by[W2]["score"] == 1.0, by[W2]      # 2 koin × 0,5
        assert by[W1]["score"] > by[W2]["score"]
        assert by[W1]["qualifies"] is True
        assert by[W2]["qualifies"] is False

        # GT-only: masuk tabel tapi TIDAK pernah lolos gate (karantina)
        si.fetch_early_buyers_ex = lambda ca, max_buyers=50, retries=2: ("ok", _t(W1))
        si.fetch_gecko_buyers_ex = lambda ca, max_pools=3: ("ok", {W1, W3})
        rows2 = si.cross_check([C1, C2])
        by2 = {r["address"]: r for r in rows2}
        assert W3 in by2, rows2
        assert by2[W3]["qualifies"] is False, by2[W3]
        assert by2[W3]["score"] == 0.5, by2[W3]    # 2 × 0,25
        print("PASS screen_insiders dual-source (2,0 both / 1,0 BS kuat / 0,5 BS lemah / 0,25 GT-only)")
    finally:
        si.fetch_early_buyers_ex = old_feb
        si.fetch_gecko_buyers_ex = old_fge


def test_is_clone_risk_zero_network():
    """REGRESI: is_clone_risk di jalur entry TIDAK boleh menyentuh jaringan.

    Kalau fungsi ini menembak Blockscout, entry tertunda 18-32 detik
    (terukur pada probe nyata) dan koin sudah kabur.
    """
    path, old = _fresh_db()
    calls = []
    old_bs = clone_guard._bs_get
    try:
        def _boom(p, timeout=8):
            calls.append(p)
            raise AssertionError(f"network dipanggil di jalur entry: {p}")
        clone_guard._bs_get = _boom
        # CA belum terdaftar sama sekali → wajib fail-open TANPA jaringan
        risk, reason = clone_guard.is_clone_risk("0x" + "9" * 40)
        assert risk is False, (risk, reason)
        assert calls == [], calls
        # register_async wajib kembali instan (< 50 ms)
        import time as _t
        t0 = _t.time()
        clone_guard.register_async("0x" + "8" * 40)
        dt = _t.time() - t0
        assert dt < 0.05, f"register_async {dt*1000:.0f} ms (harus < 50 ms)"
        print("PASS is_clone_risk nol-network + register_async instan")
    finally:
        clone_guard._bs_get = old_bs
        # tunggu thread background (yang akan gagal karena _bs_get di-restore
        # sebenarnya sudah aman: thread daemon, boleh ditinggal)
        os.unlink(path)
        config.DB_PATH = old


if __name__ == "__main__":
    test_creator_of_uses_creation_tx()
    test_creator_of_not_router()
    test_template_fp()
    test_funders_of()
    test_is_clone_risk_template_sibling()
    test_is_clone_risk_funder_family()
    test_is_clone_risk_fail_open()
    test_mark_family_failed()
    test_gecko_rate_limiter()
    test_gecko_norm_trade()
    test_gecko_failure_returns_empty()
    test_screen_insiders_dual_source_scoring()
    test_is_clone_risk_zero_network()
    print("\nALL CLONE-GUARD TESTS PASSED ✅")
