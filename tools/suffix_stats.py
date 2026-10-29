#!/usr/bin/env python3
"""Laporan rug-rate per sufiks CA (untuk memutuskan kebijakan vanity).

Dipakai untuk menguji klaim seperti "...7777 80% rug" secara empiris
sebelum menyalakan `vanity7777_enabled`. Sampel kecil gampang menipu:
di 5 trade terakhir bisa terlihat 80%, tapi di 107 posisi jadi 19%.

Pakai:
    python3 tools/suffix_stats.py                    # sufiks 3-6 digit
    python3 tools/suffix_stats.py --min-n 10         # sembunyikan sampel kecil
    python3 tools/suffix_stats.py --list 7777        # detail posisi ...7777
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sniper import config  # noqa: E402


def load(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(
        """SELECT id, symbol, ca, status, eth_spent, realized_eth,
                  ts, closed_at
             FROM positions
            WHERE status LIKE 'closed%'"""
    ))
    return c, rows


def pnl(r):
    if not r["eth_spent"]:
        return 0.0
    return (r["realized_eth"] / r["eth_spent"]) - 1.0


def summarize(rows):
    n = len(rows)
    rug = sum(1 for r in rows if r["status"] == "closed_rug")
    wiped = sum(1 for r in rows if pnl(r) <= -0.99)
    win = sum(1 for r in rows if pnl(r) > 0)
    avg = sum(pnl(r) for r in rows) / n if n else 0.0
    return {"n": n, "rug": rug, "wiped": wiped, "win": win, "avg": avg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--min-n", type=int, default=1,
                    help="sembunyikan sufiks dengan sampel < N")
    ap.add_argument("--list", default=None,
                    help="tampilkan detail posisi untuk sufiks tertentu (mis. 7777)")
    args = ap.parse_args()

    conn, rows = load(args.db)
    if not rows:
        print("Tidak ada posisi closed di DB ini.")
        return

    print(f"DB: {args.db}")
    print(f"Total posisi closed: {len(rows)}\n")

    if args.list:
        suf = str(args.list).lower()
        sel = [r for r in rows if str(r["ca"]).lower().endswith(suf)]
        print(f"=== Detail sufiks ...{suf} (n={len(sel)}) ===")
        for r in sorted(sel, key=lambda x: x["id"]):
            dur = ""
            if r["closed_at"] and r["ts"]:
                dur = f' {int(r["closed_at"] - r["ts"]):5d}s'
            print(f'  #{r["id"]:3d} {r["symbol"][:14]:14s} '
                  f'{r["status"]:15s} {pnl(r) * 100:+7.1f}%{dur}')
        s = summarize(sel)
        print(f'\n  rug {s["rug"]}/{s["n"]} ({s["rug"] / s["n"] * 100:.0f}%) · '
              f'wiped -100% {s["wiped"]} · win {s["win"]} · avg {s["avg"] * 100:+.1f}%')
        return

    buckets = {}
    for r in rows:
        ca = str(r["ca"]).lower().replace("0x", "")
        for ln in (3, 4, 5, 6):
            if len(ca) >= ln:
                buckets.setdefault((ln, ca[-ln:]), []).append(r)

    print(f'{"sufiks":8s} {"n":>4s} {"rug":>5s} {"wiped":>6s} {"win%":>6s} {"avg PnL":>9s}')
    print("-" * 46)
    for (ln, suf), sel in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
        if len(sel) < args.min_n:
            continue
        s = summarize(sel)
        print(f'{ln}d:{suf:6s} {s["n"]:4d} {s["rug"]:5d} {s["wiped"]:6d} '
              f'{s["win"] / s["n"] * 100:5.0f}% {s["avg"] * 100:+8.1f}%')

    print("\nBandingkan dengan baseline:")
    s = summarize(rows)
    print(f'  ALL      {s["n"]:4d} {s["rug"]:5d} {s["wiped"]:6d} '
          f'{s["win"] / s["n"] * 100:5.0f}% {s["avg"] * 100:+8.1f}%')


if __name__ == "__main__":
    main()
