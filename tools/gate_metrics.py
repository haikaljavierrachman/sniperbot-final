#!/usr/bin/env python3
"""🔍 CLI audit per-CA — angka SAMA persis dengan pipeline _fastpath_gate bot.

Pakai FUNGSI IDENTIK dengan bot: goldilocks_eval, safety.check,
safety.dev_pct, safety.top_holders_pct, trader.quote_buy_tokens —
jadi tidak ada perbedaan perhitungan antara layar ini dan keputusan bot.

Rules:
  exit 0 = lolos semua gate yang dijalankan
  exit 1 = SKIP (ada gagal di salah satu gate)
  exit 2 = error (CA tidak ketemu / data tidak tersedia)
TIDAK pernah mencetak private key / isi .env — hanya alamat publik & angka.
Default: evaluasi SEMUA tahap (umur, liq, mcap, rasio, dev, top10, mcap on-chain).
Flag --fast: berhenti di titik gagal pertama (persis seperti bot, hemat waktu).
"""
import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config, db, dexscreener as dx            # noqa: E402
from sniper.listener import goldilocks_eval                  # noqa: E402
from sniper import safety                                    # noqa: E402


def _fmt_usd_plain(v):
    if v is None:
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _ratio_pct(v):
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _pct1(v):
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _short_ca(ca: str) -> str:
    return f"{ca[:6]}...{ca[-4:]}" if len(ca) >= 12 else ca


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit gate per-CA (angka = perhitungan bot)")
    ap.add_argument("ca", help="token contract address 0x...")
    ap.add_argument("--fast", action="store_true",
                    help="berhenti di tahap gagal pertama (hemat); default: semua metrik")
    args = ap.parse_args()
    ca = args.ca.strip()
    if not (ca.lower().startswith("0x") and len(ca) == 42):
        print("CA tidak valid (harus 0x + 40 hex)")
        return 2

    # ---- pair Dexscreener (sumber data sama dengan bot) ----
    try:
        pair = dx.best_pair(ca)
    except Exception:
        pair = None
    if not pair:
        print("CA      : tidak ketemu di chain — exit 2")
        return 2

    sym = (pair.get("baseToken") or {}).get("symbol") or "?"
    ptype = dx.pool_type(pair)
    pair_addr = pair.get("pairAddress") or "?"

    print(f"CA      : {_short_ca(ca)}")
    print(f"simbol  : {sym}")
    print(f"pair    : {pair_addr}  ({ptype})")

    # ---- metrik dasar murni math (goldilocks_eval — TANPA I/O) ----
    ok_m, tag_m, metr = goldilocks_eval(pair)
    metr = metr or {}
    pool_age = metr.get("pool_age")
    liq = metr.get("liq")
    mcap = metr.get("mcap")
    ratio = metr.get("ratio")
    min_age = db.get("goldilocks_min_age_sec", float) or 10
    max_age = db.get("goldilocks_max_age_sec", float) or 300
    min_liq = db.get("goldilocks_min_liq", float) or 4000.0
    min_ratio = db.get("goldilocks_min_liq_mcap_ratio", float) or 0.35
    max_mcap = db.get("goldilocks_max_mcap", float) or 35000.0
    min_mcap = db.get("goldilocks_min_mcap", float) or 5000.0

    def _age_str(v):
        if v is None:
            return "—"
        try:
            return f"{float(v):,.0f}s"
        except (TypeError, ValueError):
            return "—"

    # ---- safety.bagian mahal (sama seperti bot: safety.check dulu) ----
    print()  # satu baris kosong sebelum blok angka — konsisten dengan contoh
    dev = None
    top = None
    mcap_onchain = None
    safety_reason = ""
    ok_safety = True
    if not args.fast:
        try:
            from sniper.trader import Trader
            _trader = Trader()      # angka = perhitungan bot; butuh wallet di DB/.env
            ok_safety, safety_reason = safety.check(_trader, ca, pair)
            dev = safety.dev_pct(_trader, ca)
            exclude = {pair.get("pairAddress", ""), ca}
            top = safety.top_holders_pct(_trader, ca, exclude)
            if not args.fast:
                # ---- quoter on-chain (sama seperti jalur _fastpath_gate) ----
                try:
                    buy_usd = db.get("buy_usd")
                    eth_price = dx.eth_usd(pair)
                    eth_spent = buy_usd / eth_price if eth_price > 0 else 0
                    ptype_d = ptype
                    fee = 3000
                    if ptype_d == "v3":
                        try:
                            fee = int(_trader.pool_fee(pair["pairAddress"]))
                        except Exception:
                            fee = 3000
                    wei_in = _trader.w3.to_wei(eth_spent, "ether") if eth_spent > 0 else 0
                    dec = 18
                    try:
                        dec = int(_trader.erc20(ca).functions.decimals().call())
                    except Exception:
                        dec = 18
                    ok_q, amt_raw, err_reason = _trader.quote_buy_tokens(
                        ca, ptype_d, fee, wei_in, 0, pool_id=pair["pairAddress"])
                    if ok_q and amt_raw and amt_raw > 0:
                        tokens_total = amt_raw / (10 ** dec)
                        entry_price_usd = (db.get("buy_usd") / tokens_total) \
                            if tokens_total > 0 else 0
                        total_supply = None
                        circulating_supply = None
                        try:
                            tc = _trader.erc20(ca)
                            supply_raw = tc.functions.totalSupply().call()
                            total_supply = supply_raw / (10 ** dec)
                            burned_raw = 0
                            for dead_addr in (
                                "0x000000000000000000000000000000000000dEaD",
                                "0x0000000000000000000000000000000000000000",
                            ):
                                try:
                                    burned_raw += int(
                                        tc.functions.balanceOf(dead_addr).call() or 0)
                                except Exception:
                                    pass
                            circ_raw = max(0, supply_raw - burned_raw)
                            if circ_raw > 0:
                                circulating_supply = circ_raw / (10 ** dec)
                        except Exception:
                            pass
                        if not circulating_supply or circulating_supply <= 0:
                            pair_mcap = float(pair.get("marketCap") or 0)
                            pair_price = float(pair.get("priceUsd") or 0)
                            circulating_supply = (pair_mcap / pair_price) \
                                if (pair_price > 0 and pair_mcap > 0) \
                                else 1_000_000_000
                        mcap_onchain = entry_price_usd * circulating_supply
                    else:
                        print(f"  (quoter: gagal — {err_reason})")
                except Exception as e:
                    print(f"  (quoter: error — {e.__class__.__name__})")
        except SystemExit as e:
            print(f"  (safety/quoter dilewati: {e})")
        except Exception as e:
            print(f"  (safety/quoter error: {e.__class__.__name__}: {e})")
            ok_safety = False
            safety_reason = f"error: {e.__class__.__name__}"

    # ---- tampil nilai per baris ----
    age_ok = (pool_age is not None and min_age <= pool_age <= max_age)
    liq_ok = (liq is not None and liq >= min_liq)
    mcap_ok = (mcap is not None and mcap > 0 and min_mcap <= mcap <= max_mcap)
    ratio_ok = (ratio is not None and ratio >= min_ratio)
    max_dev = db.get("goldilocks_max_dev_pct", float) or 0.04      # rasio 0-1
    max_top = db.get("goldilocks_max_top10_pct", float) or 0.30    # rasio 0-1
    dev_ok = (dev is not None and dev / 100.0 <= max_dev)
    top_ok = (top is not None and top / 100.0 <= max_top)
    oc_ok = (mcap_onchain is not None and mcap_onchain <= max_mcap)

    def _mark(ok):
        return "PASS" if ok else "FAIL"

    print(f"umur    : {_age_str(pool_age)}  [{_age_str(min_age)} .. {_age_str(max_age)}]     {_mark(age_ok)}")
    print(f"liq     : {_fmt_usd_plain(liq)}  [min {_fmt_usd_plain(min_liq)}]      {_mark(liq_ok)}")
    print(f"mcap    : {_fmt_usd_plain(mcap)}  [{_fmt_usd_plain(min_mcap)} .. {_fmt_usd_plain(max_mcap)}]     {_mark(mcap_ok)}")
    print(f"rasio   : {_ratio_pct(ratio)}  [min {_ratio_pct(min_ratio)}]      {_mark(ratio_ok)}")
    print(f"dev     : {_pct1(dev)}  [maks {float(max_dev) * 100:.1f}%]   {_mark(dev_ok)}")
    print(f"top10   : {_pct1(top)}  [maks {float(max_top) * 100:.1f}%]   {_mark(top_ok)}")
    if mcap_onchain is not None:
        print(f"onchain : {_fmt_usd_plain(mcap_onchain)}  [maks {_fmt_usd_plain(max_mcap)}]    {_mark(oc_ok)}")
    else:
        print(f"onchain : —  [maks {_fmt_usd_plain(max_mcap)}]    —")

    # ---- verdict: tahap pertama yang gagal (persis urutan bot) ----
    reasons = []
    if not ok_safety:
        reasons.append(f"safety ({safety_reason or 'dev_terlalu_besar'})")
    if not age_ok:
        reasons.append(tag_m if tag_m.startswith("age_") else "umur")
    if not liq_ok:
        reasons.append("liq_rendah")
    if not mcap_ok:
        reasons.append("mcap_out_of_range")
    if not ratio_ok:
        reasons.append("liq_ratio_rendah")
    if dev is not None and not dev_ok:
        reasons.append("dev_terlalu_besar")
    if top is not None and not top_ok:
        reasons.append("top10_terlalu_besar")
    if mcap_onchain is not None and not oc_ok:
        reasons.append("mcap_onchain_pucuk")

    if reasons:
        print(f"VERDICT : SKIP — {reasons[0]}")
        return 1
    print("VERDICT : BUY (lolos semua gate)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
