# Panduan — Mode Publik: Log Filter Koin Terbuka, Panel Khusus Admin

## Hasil akhir

| Siapa | Dapat apa |
|---|---|
| **Admin** (`ADMIN_ID` di `.env`) | Sama seperti sekarang: panel penuh via `/start` (Posisi, Sources, Settings, Stats, Otak, Wallet, tombol Sell), semua notif, hasil TP/SL, rekap, alert insider |
| **Publik** (siapa pun) | **Log filter koin + hasil exit posisi** di channel publik: kartu `SKIP …` beserta alasannya, kartu analisa/FAST-PATH, `[SIMULASI] BUY`, `BUY`, **dan** hasil exit (TP1/TP2/SL/trailing/TIME STOP/rug/inactive/TANGAN, termasuk pengumuman emergency exit) — **tanpa tombol Sell/aksi apa pun** (yang dipertahankan cuma tombol link Chart/Tx), dan angka **Saldo kas dibuang** dari versi publik |
| **Publik yang membuka bot** (`/start`) | Pesan sambutan + tombol ke channel log. **Tidak ada panel**, tidak ada data posisi/wallet/stats. Menekan tombol panel lama (dari pesan lama) dibalas: panel khusus admin |
| Perintah baru `/log` | Publik → diarahkan ke channel log. Admin → melihat status log publik (aktif/nonaktif + tujuannya) |

Yang **TIDAK pernah** disiarkan ke publik (tetap khusus admin): panel & menu
utama (alamat + saldo wallet), posisi & tombol sell, rekap harian, insting
agent, alert insider, kunci wallet, peringatan "TP ditahan (pool tipis)",
dan hasil sell MANUAL dari tombol panel (itu balasan langsung ke admin,
bukan siaran). *(Perubahan 2026-10-29 malam atas permintaan operator:
hasil exit posisi kini IKUT disiarkan — lihat CATATAN_SIARAN_EXIT.md.)*

## Cara kerja singkat

Semua log filter koin lahir di satu tempat: `listener.process_ca()`.
Notif di sana sekarang lewat `listener._notify_log()` yang mengirim ke
**dua tujuan**: admin (lengkap) dan channel publik (tombol dipangkas oleh
`listener.public_buttons()` — hanya tombol URL yang lolos).
Hasil exit disiarkan dari `engine` lewat `engine._notify_exit()` (teks
disensor `public_exit_text()`: angka Saldo kas dibuang, PnL & kas balik
tetap tampil); rekap/insting/peringatan pool-tipis di engine tetap
admin-only, dan seluruh notif `tracker` (insider) tetap admin-only.
Bila `PUBLIC_LOG_CHAT_ID` kosong, perilaku bot **persis seperti
sebelumnya** (publik mati, admin saja).

## Langkah aktifkan (sekali saja)

1. **Buat channel Telegram baru** dan set **Public** (punya username,
   mis. `@hoodlog`). Ini etalase log-nya.
2. **Add bot hoodsniper sebagai Administrator** di channel itu
   (izin *Post Messages* saja cukup).
3. **Ambil chat_id channel**: forward 1 pesan dari channel ke DM bot
   (chat admin) — bot yang sekarang sudah bisa membalas chat_id lewat
   fitur `/id`-nya. Hasilnya berbentuk `-100xxxxxxxxxx`.
4. **Isi dua baris di `.env`** di folder bot yang berjalan:

   ```
   PUBLIC_LOG_CHAT_ID=-100xxxxxxxxxx
   PUBLIC_LOG_LINK=https://t.me/hoodlog
   ```

   (`PUBLIC_LOG_LINK` = link channel publikmu; boleh dikosongkan, tapi
   tombol arahan di `/start` publik tidak akan muncul.)
5. **Restart bot**: `pm2 restart hoodsniper-v16` (sesuaikan nama prosesmu).
6. **Verifikasi** (semua dari chat admin):
   - Panel `/start` sekarang memuat baris `📣 Log publik: AKTIF`.
   - Kirim `/log` → status AKTIF + chat_id tujuan.
   - Tunggu kandidat scanner berikutnya: kartu SKIP/BUY yang sama harus
     muncul di channel publik **tanpa** tombol Sell — cuma Chart/Tx.
   - Uji dari akun Telegram kedua (bukan admin): `/start` ke bot → hanya
     sambutan + tombol channel; tidak ada panel.

## Pemasangan patch ini

Salin 5 file berikut dari paket ini menimpa folder bot yang berjalan,
lalu restart:

- `bot.py`
- `sniper/config.py`
- `sniper/control_bot.py`
- `sniper/listener.py`
- `sniper/engine.py`

Tambahan (tidak wajib untuk jalan): `tests/test_public_log.py`,
`.env.example`, dan panduan ini. File `.env` dan database **tidak**
disentuh patch — isi dua baris publiknya manual seperti langkah 4.

## Matikan lagi / troubleshooting

- **Matikan log publik**: kosongkan `PUBLIC_LOG_CHAT_ID` di `.env`, restart.
  Bot kembali persis seperti sebelumnya.
- **Log tidak muncul di channel**: hampir selalu salah satu dari —
  bot belum jadi admin channel, chat_id salah (harus `-100…` hasil
  forward, bukan username), atau bot belum direstart setelah `.env`
  diubah. Console bot mencetak `[public-log] AKTIF → …` saat start dan
  `[public notify fail] …` bila Telegram menolak kiriman.
- **Catatan mode LIVE**: kartu BUY publik menampilkan ukuran masuk &
  harga entry persis seperti kartu admin (itu memang isi log filternya).
  Selama mode latihan ini tidak berisi dana asli; kalau nanti live dan
  angka itu tidak ingin ditampilkan publik, matikan log publik atau
  minta varian kartu publik yang disensor angkanya.

## Bukti uji (workspace, sebelum kirim)

- `tests/test_public_log.py`: **14/14 lulus** — pemangkasan tombol
  (termasuk kartu SIMULASI bersarang & dua bentuk tombol Telethon),
  fan-out admin-penuh vs publik-aman, publik gagal kirim tidak
  menjatuhkan pipa, builder sambutan publik tanpa data panel, dan
  invariant bahwa `engine.py`/`tracker.py` tidak menyentuh jalur publik.
- Tombol Telethon asli (bukan stub) diverifikasi terpisah: kartu BUY
  nyata → publik hanya menerima Chart + Tx.
- Suite lama dijalankan ulang di salinan terpatch (loop sekuensial,
  DB bersama): 31/31 file rc=0. Catatan kejujuran hasil: 3 file
  (`test_fastpath_goldilocks`, `test_insider_sort_breadth`,
  `test_onchain_paper_quote`) ternyata **peka keadaan DB** — dengan
  DB benar-benar segar mereka GAGAL, tetapi gagal dengan pola yang
  **identik di zip asli tanpa patch ini**, jadi itu kondisi bawaan
  zip final (bukan regresi patch publik); dengan DB yang sudah
  terisi dari run sebelumnya, ketiganya lulus di kedua versi.
