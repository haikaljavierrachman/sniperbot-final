#!/usr/bin/env python3
"""Reset dompet kas virtual paper trading & sinkronisasi setting biaya.

Secara default menggunakan modal uji coba ketat $20.00 (bukan $100)
dan meminta konfirmasi eksplisit agar tidak sengaja menghapus profit akumulasi.

Penggunaan:
    python3 tools/reset_paper_wallet.py              # Tanya konfirmasi interaktif (default $20.00)
    python3 tools/reset_paper_wallet.py 50.0         # Tentukan modal baru (tanya konfirmasi)
    python3 tools/reset_paper_wallet.py 20.0 --force # Reset langsung tanpa konfirmasi
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sniper import config, db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Reset paper trading wallet balance safely.")
    parser.add_argument("amount", nargs="?", type=float, default=20.0,
                        help="Modal awal virtual USD (default: 20.0)")
    parser.add_argument("--force", "-y", action="store_true",
                        help="Lewati konfirmasi interaktif")
    args = parser.parse_args()

    db.init()
    current_cash = db.get("paper_cash_usd", float) or 0.0
    current_initial = db.get("paper_initial_usd", float) or 20.0
    pnl_pct = ((current_cash / current_initial) - 1.0) * 100.0 if current_initial > 0 else 0.0

    print(f"📊 Status Dompet Paper Trading Saat Ini:")
    print(f"   Modal Awal  : ${current_initial:.2f}")
    print(f"   Kas Riil    : ${current_cash:.2f} ({pnl_pct:+.2f}%)")
    print()

    if not args.force:
        print(f"⚠️  PERINGATAN: Aksi ini akan mereset saldo akumulasi profit dan menetapkan kas ke ${args.amount:.2f}!")
        try:
            confirm = input(f"Lanjutkan reset ke ${args.amount:.2f}? [y/N]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Dibatalkan.")
            return
        if confirm not in ("y", "yes"):
            print("❌ Reset dibatalkan. Saldo akumulasi Anda tetap aman.")
            return

    db.reset_paper_cash(args.amount)
    db.set_("buy_usd", "5.0")
    db.set_("paper_gas_eth", "0.00005")
    db.set_("paper_slippage_pct", "0.01")

    print("\n✅ Reset paper wallet berhasil:")
    print(f"   paper_initial_usd  = ${db.get('paper_initial_usd')}")
    print(f"   paper_cash_usd     = ${db.get('paper_cash_usd')}")
    print(f"   buy_usd            = ${db.get('buy_usd')}")
    print(f"   paper_gas_eth      = {db.get('paper_gas_eth')} ETH (~$0.12 @ L2 Robinhood)")
    print(f"   paper_slippage_pct = {db.get('paper_slippage_pct')}")


if __name__ == "__main__":
    main()
