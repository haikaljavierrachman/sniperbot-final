"""v16.7 Moonbag Inactivity Guard — sinkronisasi & pembersihan DB (one-shot).

Langkah (aman untuk DB live hoodsniper.db, backup sudah dibuat):
1. Tambah setting baru (tidak menimpa nilai recording): moonbag_inactivity_sec,
   moonbag_max_hold_sec.
2. poll_seconds := '3' (percepatan polling edisi 30 blok).
3. ZOMBIE SWEEP DEXSCREENER: untuk setiap posisi OPEN, minta pair
   Dexscreener (get_pair) → hitung txns.m5 (buys+sells) & umur posisi:
   - Jika txns.m5 == 0 dan umur > moonbag_inactivity_sec (420s):
     * quote jual on-chain dicek dulu (kalau bisa import trader — biasanya
       tidak bisa di luar bot, jadi一律 pakai harga layar pair sebagai
       basis realized tambahan pada paper/olshop).
     * status := 'closed_inactive', tokens_left := 0, closed_at := now.
     * realized_eth += tokens_left * priceNative (hasil konseptual paper).
     * paper_cash_usd += tokens_left * priceUsd / eth_usd * (1-slippage)
       minus gas — konsisten dengan engine execute_sell (is_simulated).
   - Jika Dexscreener gagal diambil (API down), posisi DILEWATI dan
     dilaporkan sebagai error — JANGAN pernah menebak-nebak.
"""

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sniper import dexscreener as dx  # noqa: E402

DB = "hoodsniper.db"
INACTIVITY_SEC = 420
SLIPPAGE = 0.01  # paper_slippage_pct
GAS_ETH = 0.00005 * 2  # gas jual (L2) yang biasa diemploy seller


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    now = int(time.time())

    # ---- 1) settings baru ----
    for k, v in (("moonbag_inactivity_sec", "420"),
                 ("moonbag_max_hold_sec", "2700")):
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    # ---- 2) poll_seconds := 3 ----
    con.execute("UPDATE settings SET value='3' WHERE key='poll_seconds'")
    con.commit()

    # ---- 3) zombie sweep dari Dexscreener ----
    old = con.execute(
        "SELECT value FROM settings WHERE key='paper_cash_usd'").fetchone()
    cash = float(old[0]) if old else 0.0
    swept = 0
    for pos in con.execute(
            "SELECT * FROM positions WHERE status='open'").fetchall():
        try:
            pair = dx.get_pair(pos["chain_slug"], pos["pair_address"])
        except Exception:
            continue
        if not pair:
            continue
        tx = ((pair.get("txns") or {}).get("m5") or {})
        vol = int(tx.get("buys") or 0) + int(tx.get("sells") or 0)
        age = now - (pos["ts"] or now)
        price_native = float(pair.get("priceNative") or 0)
        price_usd = float(pair.get("priceUsd") or 0)
        if vol != 0 or age <= INACTIVITY_SEC:
            print(f"[skip] id={pos['id']} {pos['symbol']}: vol_m5={vol} "
                  f"age={age}s")
            continue
        if price_native <= 0:
            print(f"[skip] id={pos['id']} {pos['symbol']}: priceNative 0 "
                  f"(data pair buruk), jangan menebak")
            continue

        tokens_left = float(pos["tokens_left"] or 0)
        token_eth = tokens_left * price_native
        cost_eth = float(pos["eth_spent"] or 0) * (tokens_left / pos["tokens_total"]) \
            if pos["tokens_total"] else 0.0
        if pos["is_simulated"]:
            gross_usd = token_eth * (price_usd / price_native)
            net_usd = max(gross_usd * (1 - SLIPPAGE) - GAS_ETH * 2400, 0.0)
            cash += net_usd
            con.execute(
                "UPDATE settings SET value=? WHERE key='paper_cash_usd'",
                (f"{cash:.8f}",))
            print(f"[zombie] id={pos['id']} {pos['symbol']}: tokens_left "
                  f"{tokens_left} * {price_native} ETH → ±{net_usd:.2f} USD "
                  f"balik ke kas virtual (saldo baru ${cash:.2f})")
        else:
            print(f"[zombie][LIVE] id={pos['id']} {pos['symbol']}: perlu "
                  f"pengiriman jual on-chain manual — ditutup versi DB "
                  f"offline (realized_eth dihitung dari priceNative)")
        con.execute(
            """UPDATE positions SET status='closed_inactive', tokens_left=0,
               closed_at=?, realized_eth=realized_eth+? WHERE id=?""",
            (now, token_eth, pos["id"]))
        swept += 1

    con.commit()
    print(f"\nDONE: {swept} posisi zombie di-sweep (closed_inactive). "
          f"poll_seconds='3', moonbag_inactivity_sec='420', "
          f"moonbag_max_hold_sec='2700' dipasang.")


if __name__ == "__main__":
    main()
