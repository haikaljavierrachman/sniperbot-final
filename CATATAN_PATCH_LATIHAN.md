# Catatan Patch — Pagar Keras Mode Latihan (menutup "Gagal buy: saldo ETH kurang")

## Kejadian

2026-10-29 ~21:39 WIB: notif `❌ Gagal buy WOFL dari SCANNER: saldo ETH
kurang: 0.00000 ETH` padahal bot dalam mode latihan (paper trading).

## Akar masalah (dari kode, bukan tebakan)

Paper trading di `listener.process_ca()` hanya menahan **keputusan**:
keputusan BUY dari Mata/FAST-PATH + `agent_dry_run=1` → posisi simulasi.
Pagar fallback yang lama hanya berlaku bila **Mata ON tapi gagal
menjawab** (`decision is None and agent_on=1 and agent_dry_run=1`).

Lubangnya: bila **tidak ada keputusan sama sekali** — kasus nyatanya
`entry_mode='mata'` dengan **Mata OFF** (`agent_on=0`) — kedua pagar di
atas tidak berlaku, dan alur jatuh ke `trader.buy` LIVE. Fungsi itu
mengecek saldo ETH asli lebih dulu (`trader.py`: `saldo ETH kurang`),
jadi di wallet kosong kegagalannya berbunyi persis seperti di atas.
Tidak ada dana yang bergerak — pagar fisik wallet kosong yang menahan.

Keluarga insiden yang sama dengan SURY (2026-10-22), pemicu berbeda:
dulu Mata ON + LLM gagal (sudah ditutup pagar fallback), kini Mata OFF.

## Perbaikan (hanya `sniper/listener.py`, di atas paket log publik)

Tepat sebelum satu-satunya panggilan `trader.buy` di pipa ini, ditambah
pagar mode: selama `agent_dry_run=1`, fungsi berhenti dengan status
`dry_run` + notif `🧪 MODE LATIHAN — buy LIVE ditahan` — lewat jalur
mana pun. Mode LIVE (`agent_dry_run=0`) tidak disentuh sama sekali.

## Uji

`tests/test_dryrun_live_gate.py` — 6/6 lulus, dua arah:
1. `dry_run=1` + Mata OFF → status `dry_run`, `trader.buy` tidak
   dipanggil, tidak ada notif "Gagal buy".
2. `dry_run=0` → perilaku lama persis: `trader.buy` dipanggil, `bought`.

Test terkait yang dijalankan ulang: `test_public_log`,
`test_listener_retry`, `test_userbot_decoupling` — semua rc=0.

## Pasang

Timpa `sniper/listener.py` di folder bot yang berjalan (file ini sudah
memuat patch log publik + pagar ini), lalu restart proses
(`pm2 restart final_bot`). Tidak ada perubahan `.env`/DB.
Verifikasi: kandidat scanner berikutnya yang lolos filter saat Mata
OFF tidak lagi memunculkan "Gagal buy … saldo ETH kurang", melainkan
"MODE LATIHAN — buy LIVE ditahan". Untuk posisi simulasi seperti
sebelumnya, nyalakan Mata atau set mode entry FAST-PATH di panel Otak.
