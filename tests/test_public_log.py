"""Test MODE PUBLIK — publik HANYA lihat log filter koin, panel khusus admin.

Verifikasi:
 1. listener.public_buttons: tombol callback (Sell dsb.) DIBUANG, hanya
    tombol URL (Chart/Tx) yang tersisa — termasuk struktur bersarang
    ala kartu SIMULASI BUY, dan dua bentuk tombol Telethon (lama `.url`,
    baru `.type.url`).
 2. listener._notify_log fan-out: admin menerima teks + tombol LENGKAP,
    publik menerima teks sama + tombol terpangkas; publik tidak terpasang
    → hanya admin; publik GAGAL kirim → admin tetap menerima & pipa
    tidak raise.
 3. listener.register(..., public_notify=...) memasang callback publik;
    register() gaya lama (tanpa public_notify) tetap jalan.
 4. control_bot builder publik: teks sambutan non-admin TANPA panel /
    data wallet / stats; tombolnya hanya URL ke channel log (atau None).
 5. Invariant sumber: tracker.py TIDAK mengenal log publik (alert
    insider tidak pernah disiarkan); engine.py hanya boleh menyentuh
    jalur publik lewat _notify_exit (hasil exit — lihat
    test_public_exit.py); bot.py menyerahkan public_notify ke
    listener.register dan engine.bind.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_tmp = tempfile.mkdtemp(prefix="hs_test_public_")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import importlib
import sniper.config as config
importlib.reload(config)
import sniper.db as db
importlib.reload(db)
import sniper.listener as listener
import sniper.control_bot as control_bot
control_bot.db = db
listener.db = db


# ---------- stub tombol: dua bentuk Telethon ----------
class UrlBtnOld:
    def __init__(self, url):
        self.url = url


class InlineBtnOld:
    def __init__(self, data):
        self.data = data


class _TypeUrl:
    def __init__(self, url):
        self.url = url


class _TypeCallback:
    def __init__(self, data):
        self.data = data


class UrlBtnNew:
    def __init__(self, url):
        self.type = _TypeUrl(url)


class InlineBtnNew:
    def __init__(self, data):
        self.type = _TypeCallback(data)


class TestPublicButtons(unittest.TestCase):
    def test_buy_card_strips_sell_keeps_url(self):
        chart, tx = UrlBtnOld("https://dexscreener.com/x"), UrlBtnOld("https://scan/tx/1")
        buttons = [
            [InlineBtnOld(b"sell:7:50"), InlineBtnOld(b"sell:7:100")],
            [chart, tx],
        ]
        out = listener.public_buttons(buttons)
        self.assertEqual(out, [[chart, tx]])

    def test_simulasi_card_nested_rows(self):
        # bentuk nyata kartu SIMULASI: buttons=[btn_sell(list), [url]]
        chart = UrlBtnNew("https://dexscreener.com/y")
        btn_sell = [InlineBtnNew(b"sell:3:50"), InlineBtnNew(b"sell:3:100")]
        out = listener.public_buttons([btn_sell, [chart]])
        self.assertEqual(out, [[chart]])

    def test_no_url_buttons_returns_none(self):
        self.assertIsNone(listener.public_buttons(
            [[InlineBtnOld(b"menu"), InlineBtnNew(b"pause")]]))
        self.assertIsNone(listener.public_buttons(None))
        self.assertIsNone(listener.public_buttons([]))

    def test_new_style_inline_stripped(self):
        self.assertIsNone(listener.public_buttons([[InlineBtnNew(b"sell:1:100")]]))


class TestFanout(unittest.TestCase):
    def setUp(self):
        self.admin_got, self.pub_got = [], []

        async def admin_notify(text, buttons=None):
            self.admin_got.append((text, buttons))

        async def pub_notify(text, buttons=None):
            self.pub_got.append((text, buttons))

        listener._notify = admin_notify
        listener._public_notify = pub_notify

    def tearDown(self):
        listener._notify = None
        listener._public_notify = None

    def test_admin_full_public_stripped(self):
        chart = UrlBtnOld("https://dexscreener.com/z")
        buttons = [[InlineBtnOld(b"sell:9:100")], [chart]]
        asyncio.run(listener._notify_log("🟢 BUY X", buttons=buttons))
        self.assertEqual(self.admin_got, [("🟢 BUY X", buttons)])
        self.assertEqual(self.pub_got, [("🟢 BUY X", [[chart]])])

    def test_public_unset_only_admin(self):
        listener._public_notify = None
        asyncio.run(listener._notify_log("SKIP Y"))
        self.assertEqual(len(self.admin_got), 1)
        self.assertEqual(self.pub_got, [])

    def test_public_failure_never_breaks_admin_or_pipeline(self):
        async def boom(text, buttons=None):
            raise RuntimeError("channel belum jadi admin / chat not found")
        listener._public_notify = boom
        asyncio.run(listener._notify_log("SKIP Z"))  # tidak boleh raise
        self.assertEqual(len(self.admin_got), 1)


class TestRegisterBinding(unittest.TestCase):
    def test_register_with_public_notify(self):
        async def pub(text, buttons=None):
            pass

        async def adm(text, buttons=None):
            pass

        listener.register(None, object(), adm, public_notify=pub)
        self.assertIs(listener._public_notify, pub)
        listener.bind_public_notify(None)
        self.assertIsNone(listener._public_notify)

    def test_register_legacy_positional_still_works(self):
        async def adm(text, buttons=None):
            pass

        listener.register(None, object(), adm)  # gaya lama: 3 argumen
        self.assertIs(listener._notify, adm)
        listener._notify = None


class TestPublicStartPanel(unittest.TestCase):
    def setUp(self):
        self._link = config.PUBLIC_LOG_LINK
        self._cid = config.PUBLIC_LOG_CHAT_ID

    def tearDown(self):
        config.PUBLIC_LOG_LINK = self._link
        config.PUBLIC_LOG_CHAT_ID = self._cid

    def test_text_has_no_panel_data(self):
        config.PUBLIC_LOG_LINK = "https://t.me/logpublik"
        config.PUBLIC_LOG_CHAT_ID = -100123
        txt = control_bot.public_start_text()
        self.assertIn("Log Publik", txt)
        for forbidden in ("Burner", "Kas Virtual", "Posisi Open", "buy size",
                          "PnL", "ETH"):
            self.assertNotIn(forbidden, txt)

    def test_buttons_url_only_or_none(self):
        config.PUBLIC_LOG_LINK = ""
        self.assertIsNone(control_bot.public_start_buttons())
        config.PUBLIC_LOG_LINK = "https://t.me/logpublik"
        btns = control_bot.public_start_buttons()
        self.assertEqual(len(btns), 1)
        self.assertEqual(len(btns[0]), 1)
        self.assertTrue(listener._is_url_button(btns[0][0]))


class TestSourceInvariants(unittest.TestCase):
    def _src(self, name):
        path = os.path.join(os.path.dirname(__file__), "..", name)
        with open(path) as f:
            return f.read()

    def test_tracker_never_touches_public_and_engine_only_via_exit(self):
        # tracker (insider) TIDAK pernah disiarkan — invariant tetap.
        src = self._src("sniper/tracker.py")
        self.assertNotIn("PUBLIC_LOG", src)
        self.assertNotIn("_public_notify", src)
        self.assertNotIn("_notify_log", src)
        # engine: sejak perluasan 2026-10-29 (hasil exit disiarkan),
        # engine boleh menyentuh jalur publik HANYA lewat _notify_exit
        # (detailnya di test_public_exit.py); config publik tidak dibaca
        # langsung di sini, dan fan-out filter tetap milik listener.
        esrc = self._src("sniper/engine.py")
        self.assertNotIn("PUBLIC_LOG", esrc)
        self.assertNotIn("_notify_log", esrc)

    def test_process_ca_is_the_only_fanout_source(self):
        src = self._src("sniper/listener.py")
        # semua pemanggilan fan-out berada setelah definisi process_ca
        head = src.index("async def process_ca")
        self.assertNotIn("_notify_log(", src[:head].replace(
            "async def _notify_log", "async def _NOTIFYLOG_DEF"))
        self.assertIn("_notify_log(", src[head:])

    def test_botpy_wires_public_only_to_listener(self):
        src = self._src("bot.py")
        self.assertIn("listener.register(user, trader, notify, public_notify=public_notify)", src)
        self.assertNotIn("engine.bind(trader, public_notify)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
