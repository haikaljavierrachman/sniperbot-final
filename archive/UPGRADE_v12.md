# hoodsniper v12 — Filter Sosmed + Target Publik/Private

Update dari v11. Gak ada perubahan skema DB yang breaking (setting baru auto-insert saat `db.init()`).

## Apa yang baru

### 1. Target channel PUBLIK atau PRIVATE
Waktu ➕ Tambah Source, sekarang bisa kirim:

**Publik** (auto-resolve pakai akun userbot):
```
@degencalls 30000 Degen Public
https://t.me/degencalls 30000 Degen Public
https://t.me/degencalls/12 30000 Degen Topic   (12 = topic id, opsional)
```

**Private** (akun harus sudah join dulu):
```
-1001234567890 15000 Alpha Private
https://t.me/c/2103131992/250551 30000 Degen Hood
```

> Syarat mutlak: akun Telegram (userbot) **wajib sudah join** channel-nya, publik maupun private. Resolver publik butuh userbot aktif (`get_entity`).

### 2. Filter Sosmed dengan pilihan (X / Web / TG / dll)
Panel: **⚙️ Settings → 🌐 Filter sosmed** → muncul tombol mode:

| Mode    | Lolos kalau…                         |
|---------|--------------------------------------|
| Off     | filter mati, semua lolos             |
| Any     | ada min 1 sosmed apa aja             |
| X/Twitter | punya X/Twitter                    |
| Telegram | punya Telegram                      |
| X + TG  | punya X **dan** Telegram             |
| Website | punya website                        |
| Min N   | jumlah link sosmed ≥ `social_min_count` |

Setting pendukung:
- `social_min_count` — angka minimal link buat mode "Min N".
- `social_fail_closed` — `1` = token yang info sosmednya belum ke-index Dexscreener (token menit-0) **di-skip**; `0` = tetap lolos (fail-open). Toggle via tombol 🔒 di panel filter.

Data jejak diambil dari Dexscreener `pair.info` (websites + socials). Token yang ke-skip filter dapat notif `🌐 SKIP …`, dan notif BUY sekarang nampilin baris `🌐` (jejak sosmed yang lolos).

## File yang berubah
- `sniper/config.py` — 3 setting + label baru.
- `sniper/dexscreener.py` — `socials_of()`, `social_ok()`, `SOCIAL_MODE_LABEL`.
- `sniper/listener.py` — filter sosmed disisipkan sebelum `safety.check` + baris 🌐 di notif buy.
- `sniper/control_bot.py` — panel picker sosmed + resolver target publik/private.
- `bot.py` — oper userbot ke `control_bot.register(...)`.

## Cara update di VPS
```bash
# backup db dulu
cp hoodsniper.db hoodsniper.db.bak
# ganti folder sniper/ + bot.py dengan versi baru (jangan timpa .env & .db)
pm2 restart hoodsniper
```
Setelah restart, buka menu → Settings → 🌐 Filter sosmed, pilih mode. Rekomendasi awal buat degen early: **Any** + fail-closed OFF; perketat ke **X** / **X+TG** kalau mau win-rate lebih tinggi (trade-off: entry lebih sedikit).

## Bugfix pass (review v12)
- **Notif BUY**: dulu nampilin `🌐 social off` kalau filter mati; sekarang selalu nampilin jejak sosmed asli token (`🌐 sosmed: web·X·tg` atau `tanpa jejak sosmed`).
- **Resolve channel publik**: angka di belakang link publik (`t.me/username/123` = ID pesan) dulu salah dianggap topic → source diam-diam gak pernah match. Sekarang channel publik selalu `topic 0` (pantau semua pesan). Forum privat tetap via `t.me/c/<internal>/<topic>`.
- **Picker sosmed**: tap mode yang lagi aktif dulu munculin popup error (`MessageNotModified`); sekarang di-guard (no-op).
- Pesan sukses tambah source sekarang ngingetin biar akun userbot udah join channel-nya.

## Command baru: /id (cari chat_id private)
Di bot control, ketik `/id` lalu **forward** 1 pesan dari channel/grup private → bot balas `chat_id`-nya (format `-100...`), langsung siap dipaste ke ➕ Tambah Source. Kalau channel restrict forward / sumber disembunyikan, bot arahin pakai **Copy Message Link**.

## Mulai bersih + multi-source bebas
- Auto-seed dimatikan (`SEED_GROUP_ID` default 0) → DB baru mulai kosong, tinggal ➕ tambah channel sendiri.
- Tambah channel/grup **publik maupun private tanpa batas**, tiap source independen (gak ada syarat "minimal N channel setuju"). Toggle on/off & hapus per-source.
- `SEED_GROUP_ID` di `.env` masih bisa diisi kalau mau auto-seed source default.

## Wallet EVM (CLI)
- `python bot.py wallet` — tampilkan address wallet aktif (dari .env).
- `python bot.py wallet new` — generate wallet EVM baru, simpan ke .env (chmod 600), tampilkan address + private key sekali (backup!). ⚠️ hot wallet di VPS → isi burner secukupnya.
- `python bot.py wallet import 0x<key>` — import private key yang sudah ada ke .env.
- **Bugfix**: `config._i` sekarang tahan nilai .env kosong (`TG_API_ID=` blank) — dulu bikin crash `int('')` bahkan sebelum bot jalan.

## Multi-wallet lewat bot (gaya trading bot)
Menu utama → **👛 Wallet**:
- **➕ Generate** — bikin wallet EVM baru; bot tampilkan address + private key (sekali) dengan peringatan backup. Wallet pertama otomatis jadi aktif.
- **📥 Import** — paste private key lama.
- **⭐ Pakai** — pilih wallet aktif (yang dipakai buat beli); langsung ganti runtime tanpa restart.
- **🔑 Key** — tampilkan ulang private key (buat backup), **🗑** hapus dari bot.
- Wallet aktif nampilin saldo ETH.
- Key disimpan di DB; isi `WALLET_SECRET` di `.env` buat enkripsi (pakai keystore eth-account, tanpa dependency tambahan). Kosong = plaintext + DB sebaiknya chmod 600.
- Trader ambil wallet aktif dari DB dulu, fallback ke `PRIVATE_KEY` di `.env`. `PRIVATE_KEY` gak wajib lagi kalau pakai wallet dari bot.

⚠️ **Keamanan**: private key muncul di chat Telegram — backup lalu hapus pesannya. Ini hot wallet di VPS, isi burner secukupnya.
