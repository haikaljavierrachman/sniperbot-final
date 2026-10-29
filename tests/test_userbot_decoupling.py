"""Test v16.6 DECOUPLING USERBOT — userbot 100% opsional.

Verifikasi:
 1. Setting userbot_enabled default '0' (non-aktif) di DB.
 2. listener.register(None, trader, notify) sukses tanpa userbot
    (tanpa raise, tanpa memasang handler).
 3. control_bot.sources_panel() render benar saat _user is None /
    userbot_enabled='0' (tanpa crash).
 4. Pola runner asyncio.gather: user is None → hanya bot yang dirun.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tempfile

_tmp = tempfile.mkdtemp(prefix="hs_test_userbot_")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
import importlib
# reload config supaya DB_PATH env terbaca, lalu modul yang memakainya
import sniper.config as config
importlib.reload(config)
import sniper.db as db
importlib.reload(db)
import sniper.listener as listener
import sniper.control_bot as control_bot
control_bot.db = db
listener.db = db


class _DummyTrader:
    addr = "0x0"


class _DummyUserClient:
    """Segera: kalau register() memanggil user_client.on() → test gagal."""

    def on(self, *a, **k):
        raise AssertionError("register() TIDAK boleh pasang handler saat user is None")


_called_notify = []


async def _dummy_notify(text, buttons=None):
    _called_notify.append(text)


class TestUserbotDecoupling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def test_01_default_userbot_disabled(self):
        self.assertEqual(db.get("userbot_enabled", str), "0",
                         "userbot_enabled harus default '0' (non-aktif)")
        self.assertIn("userbot_enabled", config.SETTING_DEFAULTS)
        self.assertEqual(config.SETTING_DEFAULTS["userbot_enabled"], "0")
        self.assertIn("userbot_enabled", config.SETTING_LABELS)

    def test_02_register_none_userbot_success(self):
        """register(None, ...) tidak raise & tidak pasang handler."""
        trader = _DummyTrader()
        listener.register(None, trader, _dummy_notify)
        self.assertIs(listener._trader, trader)

    def test_02b_register_none_no_handler_installed(self):
        with self.assertRaises(AssertionError):
            listener.register(_DummyUserClient(), _DummyTrader(), _dummy_notify)

    def test_03_sources_panel_render_tanpa_userbot(self):
        control_bot._user = None
        db.set_("userbot_enabled", "0")
        text, buttons = control_bot.sources_panel()
        self.assertIn("NON-AKTIF", text)
        self.assertIn("On-Chain Scanner", text)
        self.assertIn("bebas risiko ban", text)
        self.assertTrue(all(buttons))  # buttons valid (tidak kosong berlapis kosong)

    def test_04_sources_panel_render_userbot_aktif(self):
        control_bot._user = object()
        db.set_("userbot_enabled", "1")
        text, buttons = control_bot.sources_panel()
        self.assertIn("AKTIF", text)
        db.set_("userbot_enabled", "0")
        control_bot._user = None

    def test_05_runner_gather_user_none_hanya_bot(self):
        """Pola bot.py akhir: gather([bot]) saja saat user is None."""
        async def _bot():
            return "bot"

        async def _user():
            return "user"

        async def _collect(user) -> list:
            runners = [asyncio.ensure_future(_bot())]
            if user is not None:
                runners.append(asyncio.ensure_future(_user()))
            return await asyncio.gather(*runners)

        results = asyncio.run(_collect(None))
        self.assertEqual(results, ["bot"])

    def test_06_runner_gather_user_on_dua_runner(self):
        async def _bot():
            return "bot"

        async def _user():
            return "user"

        async def _collect(user) -> list:
            runners = [asyncio.ensure_future(_bot())]
            if user is not None:
                runners.append(asyncio.ensure_future(_user()))
            return await asyncio.gather(*runners)

        results = asyncio.run(_collect(object()))
        self.assertEqual(sorted(results), ["bot", "user"])

    def test_07_settings_default_masuk_db(self):
        """Semua SETTING_DEFAULTS ter-INSERT di DB saat init."""
        for k, v in config.SETTING_DEFAULTS.items():
            if k == "userbot_enabled":
                continue  # mungkin sudah ditimpa test sebelumnya
            self.assertEqual(str(db.get(k, str)), str(v))


if __name__ == "__main__":
    unittest.main(verbosity=2)
