"""🧪 Uji kebijakan CA vanity ...7777 (v16.9 Opsi B) — tanpa jaringan.

Latar: klaim "80% rug berasal dari cetakan ...7777" TIDAK terbukti pada
107 posisi closed (rug rate 7777 19% vs non-7777 18%). Kebijakan karena
itu dibuat OPSIONAL dan default MATI. Yang diuji di sini adalah
mekanisme switch-nya, bukan khasiatnya.

Lingkup:
  1. is_vanity_7777 benar untuk 0x..., tanpa prefix, dan huruf besar.
  2. Default (vanity7777_enabled=0) → time-stop TIDAK berubah.
  3. enabled=1 → CA 7777 dipersingkat ke min(base, cap).
  4. enabled=1 → CA non-7777 tetap pakai base.
  5. cap=0 → tidak mempersingkat (dianggap off).
  6. base=0 (time-stop global off) → tetap 0.
  7. Setting default di config: enabled=0, cap=120.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db, engine   # noqa: E402

CA7777 = "0x" + "ab" * 19 + "7777"
CA_NORMAL = "0x" + "cd" * 20


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = config.DB_PATH
    config.DB_PATH = tmp.name
    db.init()
    return tmp.name, old


def test_detector():
    assert engine.is_vanity_7777(CA7777)
    assert engine.is_vanity_7777(CA7777.upper().replace("0X", "0x"))
    assert engine.is_vanity_7777(CA7777[2:])          # tanpa 0x
    assert not engine.is_vanity_7777(CA_NORMAL)
    assert not engine.is_vanity_7777("")
    assert not engine.is_vanity_7777(None)
    # 7777 di tengah/awal bukan vanity suffix
    assert not engine.is_vanity_7777("0x7777" + "ef" * 18)
    print("PASS detektor sufiks 7777")


def test_default_disabled_keeps_base():
    path, old = _fresh_db()
    try:
        assert config.SETTING_DEFAULTS["vanity7777_enabled"] == "0"
        assert config.SETTING_DEFAULTS["vanity7777_max_hold_sec"] == "120"
        assert engine.effective_max_hold({"ca": CA7777}, 240) == 240
        print("PASS default mati → time-stop 240s tidak berubah")
    finally:
        os.unlink(path)
        config.DB_PATH = old


def test_enabled_shortens_7777_only():
    path, old = _fresh_db()
    try:
        db.set_("vanity7777_enabled", "1")
        assert engine.effective_max_hold({"ca": CA7777}, 240) == 120
        assert engine.effective_max_hold({"ca": CA_NORMAL}, 240) == 240
        # base lebih pendek dari cap → pakai base (tidak memperpanjang)
        assert engine.effective_max_hold({"ca": CA7777}, 90) == 90
        print("PASS aktif → 7777=120s, non-7777=240s, tidak memperpanjang")
    finally:
        os.unlink(path)
        config.DB_PATH = old


def test_cap_zero_and_global_off():
    path, old = _fresh_db()
    try:
        db.set_("vanity7777_enabled", "1")
        db.set_("vanity7777_max_hold_sec", "0")
        assert engine.effective_max_hold({"ca": CA7777}, 240) == 240

        db.set_("vanity7777_max_hold_sec", "120")
        assert engine.effective_max_hold({"ca": CA7777}, 0) == 0
        print("PASS cap=0 no-op & time-stop global off tetap off")
    finally:
        os.unlink(path)
        config.DB_PATH = old


def test_labels_present():
    for key in ("vanity7777_enabled", "vanity7777_max_hold_sec"):
        assert key in config.SETTING_LABELS, key
        assert "7777" in config.SETTING_LABELS[key]
    print("PASS label setting tampil di menu")


if __name__ == "__main__":
    test_detector()
    test_default_disabled_keeps_base()
    test_enabled_shortens_7777_only()
    test_cap_zero_and_global_off()
    test_labels_present()
    print("\n✅ SEMUA TES OPSI B (kebijakan 7777) LULUS")
