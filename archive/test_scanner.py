#!/usr/bin/env python3
"""Tes standalone scanner (v16 gabungan) — SATU siklus penemuan + filter, READ-ONLY.

Tidak login Telegram, tidak membuat Trader, tidak memanggil LLM, dan TIDAK
PERNAH memanggil listener.process_ca → mustahil membeli apa pun. Gunanya:
melihat kandidat apa saja yang ditemukan scanner dari pasar saat ini dan
di tahap mana saja mereka gugur di funnel filter pasar.

Pakai (dari folder proyek):
    python test_scanner.py             # sumber: profil/boost Dexscreener
    python test_scanner.py --factory   # + kandidat event PoolCreated factory v3
"""
import sys
import time

from sniper import config, db
from sniper import scanner
from sniper import dexscreener as dx


def _age_str(pair) -> str:
    created = pair.get("pairCreatedAt")
    if not created:
        return "?"
    mins = (time.time() * 1000 - float(created)) / 60000.0
    if mins < 60:
        return f"{mins:.0f}mnt"
    if mins < 1440:
        return f"{mins / 60:.1f}jam"
    return f"{mins / 1440:.1f}hari"


def main():
    db.init()
    print(f"chain target : {config.DEX_CHAIN_MATCH}")
    print(f"ambang       : liq>=${db.get('scanner_min_liq'):,.0f} · "
          f"vol1j>=${db.get('scanner_min_vol_h1'):,.0f} · "
          f"txns1j>={db.get('scanner_min_txns_h1'):.0f} · "
          f"umur maks {db.get('scanner_max_age_min'):.0f} mnt")

    cands = scanner.fetch_profile_candidates()
    print(f"\n[1] profil/boost Dexscreener : {len(cands)} kandidat unik chain ini")

    if "--factory" in sys.argv:
        w3 = scanner._make_w3()
        fc = scanner.fetch_factory_candidates(w3) if w3 else []
        print(f"[1b] event factory v3        : {len(fc)} kandidat")
        cands += [c for c in fc if c not in cands]

    fresh = [c for c in cands if not db.seen_call(c)]
    print(f"[2] belum pernah dilihat     : {len(fresh)} "
          f"({len(cands) - len(fresh)} sudah ada di riwayat calls)")

    gagal = {}
    lolos = []
    for ca in fresh:
        try:
            pair = dx.best_pair(ca)
        except Exception as e:
            gagal["ambil_pair_error"] = gagal.get("ambil_pair_error", 0) + 1
            print(f"    ! best_pair error {ca}: {e}")
            continue
        if not pair:
            gagal["pair_tidak_ada"] = gagal.get("pair_tidak_ada", 0) + 1
            continue
        ok, reason = scanner.market_ok(pair)
        if not ok:
            kode = reason.split(" ")[0]
            gagal[kode] = gagal.get(kode, 0) + 1
            continue
        lolos.append({"ca": ca, "pair": pair, "score": scanner.score_pair(pair)})

    print(f"[3] gugur di filter pasar    : {sum(gagal.values())} {gagal if gagal else ''}")
    print(f"[4] lolos funnel pasar       : {len(lolos)}")

    lolos.sort(key=lambda r: r["score"], reverse=True)
    print("\n=== TOP-10 KANDIDAT (diurutkan skor) ===")
    print(f"{'SYM':<10} {'MCAP':>12} {'LIQ':>12} {'VOL1J':>12} {'TX1J':>6} {'UMUR':>7} {'SKOR':>6}  CA")
    for r in lolos[:10]:
        p = r["pair"]
        sym = (p.get("baseToken", {}).get("symbol") or "?")[:9]
        h1 = (p.get("txns") or {}).get("h1") or {}
        ntx = int(h1.get("buys") or 0) + int(h1.get("sells") or 0)
        print(f"{sym:<10} ${dx.mcap_of(p):>11,.0f} ${dx.liq_usd(p):>11,.0f} "
              f"${float((p.get('volume') or {}).get('h1') or 0):>11,.0f} {ntx:>6} "
              f"{_age_str(p):>7} {r['score']:>6.2f}  {r['ca']}")
    if not lolos:
        print("(kosong — tidak ada kandidat yang lolos funnel siklus ini)")
    print("\nCatatan: ini baru lolos funnel PASAR. Di bot sungguhan mereka masih "
          "harus melewati filter mcap/sosmed/safety/Mata di process_ca, dan "
          "eksekusinya mengikuti mode global (agent_dry_run default ON = "
          "berhenti di notif MODE LATIHAN).")


if __name__ == "__main__":
    main()
