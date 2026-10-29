"""Test SIARAN HASIL EXIT ke channel publik (perluasan 2026-10-29).

Permintaan operator: bukan cuma log filter yang disiarkan — HASIL EXIT
posisi juga (teks execute_sell TP1/TP2/SL/trail/timeout/rug/inactive,
pengumuman emergency exit, exit TANGAN, kegagalan exit). Yang tetap
admin-only dari engine: rekap harian, insting agent, peringatan
"TP ditahan (pool tipis)".

Verifikasi:
 1. engine.public_exit_text membuang angka "Saldo kas: $X" tetapi
    mempertahankan PnL terealisasi & kas balik per posisi (contoh teks
    persis kartu TIME STOP FOREVER yang dilihat operator).
 2. engine._notify_exit fan-out: admin menerima teks ASLI (dengan
    saldo kas), publik menerima teks tersensor; publik tidak terpasang
    → hanya admin; publik gagal kirim → admin tetap menerima.
 3. engine.bind(trader, notify) gaya lama tetap jalan; bind dengan
    public_notify memasang callback publik.
 4. Invariant sumber: semua panggilan execute_sell di engine lewat
    _notify_exit; rekap & insting tetap _notify biasa; peringatan pool
    tipis tetap _notify biasa.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_tmp = tempfile.mkdtemp(prefix="hs_test_public_exit_")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import importlib
import sniper.config as config
importlib.reload(config)
import sniper.db as db
importlib.reload(db)
import sniper.engine as engine

# Contoh nyata: kartu TIME STOP (TIMEOUT) FOREVER, 2026-10-29 21:57 WIB
TIME_STOP_CARD = (
    "⏱️ **TIME STOP (TIMEOUT 4m)** 🧪 **[SIMULASI]** FOREVER (`0xfc48c148…`)\n"
    "tidak tembus TP dalam batas waktu:\n"
    "layar +26.5% → **realized +32.4%**\n"
    "42,192 token → **0.002823 ETH** masuk · gas 0.000050 🔍 tx\n"
    "💵 Kas balik (net): **+$6.25** (bruto $6.49 - gas $0.23 - slip 2%) | "
    "Saldo kas: **$21.02**\n"
    "posisi CLOSED"
)


class TestPublicExitText(unittest.TestCase):
    def test_saldo_kas_stripped_pnl_kept(self):
        out = engine.public_exit_text(TIME_STOP_CARD)
        self.assertNotIn("Saldo kas", out)
        self.assertNotIn("21.02", out)
        self.assertIn("realized +32.4%", out)
        self.assertIn("Kas balik (net): **+$6.25**", out)
        self.assertIn("(bruto $6.49 - gas $0.23 - slip 2%)", out)
        self.assertIn("posisi CLOSED", out)

    def test_text_without_saldo_unchanged(self):
        t = "🎯 **TP1** XYZ\nlayar +50.0% → **realized +48.0%**"
        self.assertEqual(engine.public_exit_text(t), t)


class TestExitFanout(unittest.TestCase):
    def setUp(self):
        self.admin_got, self.pub_got = [], []

        async def admin_notify(text, buttons=None):
            self.admin_got.append(text)

        async def pub_notify(text, buttons=None):
            self.pub_got.append(text)

        engine._notify = admin_notify
        engine._public_notify = pub_notify

    def tearDown(self):
        engine._notify = None
        engine._public_notify = None

    def test_admin_full_public_sanitized(self):
        asyncio.run(engine._notify_exit(TIME_STOP_CARD))
        self.assertEqual(self.admin_got, [TIME_STOP_CARD])
        self.assertEqual(len(self.pub_got), 1)
        self.assertNotIn("Saldo kas", self.pub_got[0])
        self.assertIn("realized +32.4%", self.pub_got[0])

    def test_public_unset_only_admin(self):
        engine._public_notify = None
        asyncio.run(engine._notify_exit(TIME_STOP_CARD))
        self.assertEqual(len(self.admin_got), 1)
        self.assertEqual(self.pub_got, [])

    def test_public_failure_never_breaks_admin(self):
        async def boom(text, buttons=None):
            raise RuntimeError("can't write in this chat")
        engine._public_notify = boom
        asyncio.run(engine._notify_exit(TIME_STOP_CARD))  # tidak boleh raise
        self.assertEqual(len(self.admin_got), 1)


class TestBind(unittest.TestCase):
    def tearDown(self):
        engine._public_notify = None

    def test_bind_with_public_notify(self):
        async def pub(text, buttons=None):
            pass

        async def adm(text, buttons=None):
            pass

        engine.bind(object(), adm, public_notify=pub)
        self.assertIs(engine._public_notify, pub)

    def test_bind_legacy_two_args(self):
        async def adm(text, buttons=None):
            pass

        engine.bind(object(), adm)  # gaya lama: tanpa public_notify
        self.assertIs(engine._notify, adm)


class TestSourceInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "sniper", "engine.py")
        with open(path) as f:
            cls.src = f.read()

    def test_all_execute_sell_results_go_public(self):
        self.assertNotIn("await _notify(await execute_sell(", self.src)
        self.assertGreaterEqual(
            self.src.count("await _notify_exit(await execute_sell("), 10)

    def test_recap_and_insting_stay_admin_only(self):
        self.assertIn("await _notify(build_recap(", self.src)
        self.assertIn('await _notify("💡 **Insting baru', self.src)

    def test_thin_warnings_stay_admin_only(self):
        # peringatan "TP ditahan, tunggu depth" BUKAN hasil exit
        for frag in ("TP2 ditahan, tunggu depth", "TP1 ditahan, tunggu depth"):
            i = self.src.index(frag)
            j = self.src.rfind("await _notify", 0, i)
            self.assertNotIn("_notify_exit", self.src[j:j + 20], frag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
