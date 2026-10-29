# Hoodsniper v16.8

Bot sniper on-chain untuk Robinhood Chain (chain id `4663`). Seluruh eksekusi entry dan exit berjalan murni di Uniswap v4, ditambah radar terpisah untuk memantau pergerakan dompet insider.

Bukan bot "AI trading". Logika entry dan exit utamanya aritmatika serta pembacaan state pool langsung dari kontrak `PoolManager`, `StateView`, dan `V4Quoter`. LLM cuma dipakai kalau mode MATA dinyalakan, dan itu opsional.

## Yang dipantau

Ada dua jalur pemantauan on-chain dengan fungsi berbeda:

- **Scanner Uniswap v4 (jalur eksekusi buy):** memantau event `Initialize` langsung dari kontrak `PoolManager` Uniswap v4. Jalur lama Uniswap v2, v3, dan Pons sudah dihapus total. Pool baru yang belum ter-index atau masih menunggu likuiditas disimpan di antrean `pending_pools` dan dicoba ulang tiap 20 detik sampai memenuhi syarat atau lewat batas umur.
- **Insider Tracker (jalur radar alert-only):** memantau transaksi beli dari daftar dompet insider aktif, tapi diputus penuh dari pipa buy otomatis. Sinyal insider hanya dikirim sebagai notifikasi radar ke Telegram (dengan dedup 10 menit per token) dan dicatat ke menu **Log Beli Insider** (`10m`, `1 Jam`, `24 Jam`) untuk melihat token mana yang paling ramai diakumulasi insider.

Daftar dompet insider tetap bisa dirotasi otomatis tiap 6 jam atau dipangkas manual lewat panel Telegram.

## Gate entry

Urutannya dibuat dari pemeriksaan paling ringan ke yang paling mahal.

Umur pool 10 sampai 300 detik. Di bawah 10 detik terlalu mepet dengan blok deploy, di atas 5 menit dianggap bukan peluncuran baru.

Likuiditas minimal $4.000. Market cap $5.000 sampai $35.000. Rasio likuiditas terhadap market cap minimal 35%.

Audit keamanan bersifat fail-closed: cek fungsi berbahaya di bytecode (`mint`, `blacklist`, `setFee`), porsi dev maksimal 4% supply, dan top 10 holder di luar pool serta kontrak maksimal 30%. Kalau Blockscout atau RPC gagal merespons, token langsung ditolak.

Terakhir quoter on-chain (`V4Quoter`) menghitung harga spot asli dan market cap riil setelah dikurangi token yang sudah dibakar ke `0x...dEaD` dan `0x000...000`. Kalau market cap hasil quoter tembus di atas $35.000, entry dibatalkan. Semua angka market cap di notifikasi buy menggunakan angka on-chain ini, bukan angka layar Dexscreener yang sering terlambat.

## Dua mode entry

**FAST-PATH** (default). Semua pemeriksaan di atas jalan dalam hitungan milidetik tanpa memanggil LLM.

**MATA**. LLM membaca metrik dan memberi skor conviction sebelum masuk. Kalau LLM gagal menjawab, bot menahan entry.

## Exit

Parameter exit disesuaikan untuk menghindari pola pencabutan LP (rug pull) di Uniswap v4 yang rata-rata terjadi di menit ke-5 sampai ke-7:

- **TP1 di +20%**, jual 60% posisi untuk mengamankan modal pokok lebih cepat.
- **TP2 di +45%**, jual 100% sisa posisi (tanpa menyisakan moonbag yang rawan hangus saat pool dicabut).
- **Stop loss -30%**, atau tutup darurat seketika kalau likuiditas on-chain v4 terdeteksi jatuh ke nol.
- **Time-stop 240 detik (4 menit)**. Posisi yang belum selesai di menit ke-4 langsung ditutup bersih sebelum masuk jendela rawan cabut LP.

Perhitungan PnL, harga puncak (`peak_price`), dan trailing stop semuanya memakai harga hasil `quote_sell_eth` dari `V4Quoter` on-chain, bukan harga cache layar Dexscreener.

## Pagar tambahan

Clone guard. Keluarga token dikenali dari `sha256(bytecode[:1000])`, nama/simbol, dan jaringan dompet pendana (funder) deployer. Kalau satu token dalam keluarga ketahuan rug, kloningan berikutnya dari jaringan operator yang sama langsung ditolak.

Rate limit keluarga. Maksimal 3 entry per keluarga operator per 24 jam supaya bot tidak berulang kali masuk ke pabrik token yang sama.

## Mode latihan

Default berjalan di paper trading (simulasi). Jumlah token saat beli dan ETH hasil jual dihitung langsung dari kuoter Uniswap v4 on-chain, ditambah potongan spread entry 2,5%, slippage, dan beban gas.

Saldo virtual bisa di-reset kapan saja dengan nominal USD bebas lewat tombol **Reset Saldo** di menu **Wallet** Telegram.

Mode live butuh wallet burner terpisah dan `WALLET_PRIVATE_KEY` di `.env`.

## Telegram

Panel kontrol memakai Bot API resmi tanpa userbot.

Notifikasi buy dan skip menampilkan metrik gate lengkap. Di dalam menu **Sources** tersedia panel **Insider** untuk mengelola daftar dompet serta tombol **Log Beli Insider** untuk memantau peringkat koin yang sedang banyak dibeli insider.

Untuk menguji satu CA langsung dari terminal tanpa menunggu siklus bot:

```bash
python3 tools/gate_metrics.py 0x...
```

## Menjalankan

Prasyarat: Linux, Python 3.11+, RPC Robinhood Chain, token bot dari BotFather, dan satu wallet burner.

```bash
git clone git@github.com:irf4an/sniperboot-v16.git
cd sniperboot-v16
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Isi `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `RPC_URL`, `WALLET_PRIVATE_KEY`.

```bash
bash tests/run_all.sh
pm2 start ecosystem.config.js
pm2 logs hoodsniper-v16
```

## Keamanan

Jangan buka port ke publik. Akses remote cukup lewat Tailscale atau Cloudflare Tunnel.

Selalu gunakan wallet burner dengan saldo terukur, jangan pernah memasukkan private key dompet utama.

Pastikan `.env` dan file database memiliki izin akses `chmod 600`.
