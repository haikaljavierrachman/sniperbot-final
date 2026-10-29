# Catatan Perluasan — Hasil Exit Ikut Disiarkan ke Channel Publik

## Permintaan

2026-10-29 21:58 WIB, operator: "Siarkan juga hasil exit ke channel"
(menanggapi kartu TIME STOP FOREVER yang tidak tayang di channel karena
desain awal hanya menyiarkan log filter).

## Yang berubah (di atas paket log publik + pagar latihan)

- `sniper/engine.py`:
  - `bind(trader, notify, public_notify=None)` + `bind_public_notify()`.
  - `engine._notify_exit(text, buttons=None)`: admin menerima teks
    asli; publik menerima teks tersensor + tombol dipangkas (hanya URL,
    via `listener.public_buttons`). Gagal kirim ke publik tidak pernah
    menggagalkan notif admin/monitor.
  - `engine.public_exit_text()`: membuang segmen `| Saldo kas: $X`
    dari teks publik. PnL terealisasi, kas balik, jumlah token, dan ETH
    masuk tetap tampil — itu isi hasil exit yang diminta disiarkan.
  - 21 titik notif diganti ke `_notify_exit`: semua hasil
    `execute_sell` (TP1/TP2/SL/trail/timeout/rug/inactive), 5 pengumuman
    emergency exit (rug on-chain, liq anjlok, LP v4 = 0, EXIT INACTIVE,
    HARVEST FULL), exit oleh TANGAN (50%/ALL), kegagalan exit
    (emergency gagal, gagal eksekusi sell), dan penutupan pembersihan
    (sisa 0 → ditutup otomatis).
- `bot.py`: `engine.bind(trader, notify, public_notify=public_notify)`.

## Yang SENGAJA tetap admin-only

- Rekap harian & insting agent (bukan hasil exit).
- Peringatan "🫧 TP ditahan — pool tipis" (posisi belum exit).
- Alert insider (tracker tidak disentuh sama sekali).
- Hasil sell MANUAL dari tombol panel: itu `event.respond` langsung ke
  chat admin di `control_bot`, bukan siaran notify — tidak disiarkan.
- Panel, wallet, posisi, settings — tidak berubah.

## Uji

- `tests/test_public_exit.py` — **10/10 lulus**: sensor Saldo kas pada
  contoh kartu TIME STOP asli (PnL +32,4% & kas balik dipertahankan),
  fan-out admin-asli vs publik-tersensor, publik mati/gagal tidak
  berdampak, bind lama & baru, dan invariant sumber (semua
  `execute_sell` lewat `_notify_exit`; rekap/insting/peringatan tipis
  tetap `_notify`).
- Disesuaikan: `tests/test_public_log.py` (invariant tracker tetap;
  invariant engine dilonggarkan khusus jalur exit) — lulus.
- Regresi terkait: `test_3layer_exit`, `test_time_stop_and_intervals`,
  `test_virtual_paper_trading`, `test_dryrun_live_gate`,
  `test_tracker` — semua rc=0.

## Pasang

Timpa `sniper/engine.py` + `bot.py` di folder bot yang berjalan
(`sniper/listener.py` terbaru dari paket yang sama sekalian, bila pagar
latihan belum terpasang), lalu restart (`pm2 restart final_bot`).
Tidak ada perubahan `.env`/DB — tujuan siaran tetap
`PUBLIC_LOG_CHAT_ID` yang sama.

Verifikasi: posisi simulasi berikutnya yang tertutup (TP/SL/TIME STOP)
harus tayang di channel **tanpa** baris Saldo kas, sementara versi
admin tetap lengkap. Prasyarat lama tetap berlaku: bot harus admin
channel, kalau tidak log server mencetak `[public-exit fail]`.
