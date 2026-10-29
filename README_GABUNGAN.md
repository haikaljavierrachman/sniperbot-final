# Hoodsniper v16 GABUNGAN — Listener Grup + Scanner dalam Satu Bot

v16 menggabungkan dua metode penemuan sinyal yang sebelumnya hidup di
versi terpisah, menjadi **satu proses, satu database, satu Jejak**:

```
Grup/Channel Telegram ──CA──┐
                             ├──▶ filter mcap/liq ─▶ sosmed ─▶ safety ─▶ 👁 Mata ─▶ buy ─▶ monitor TP/SL
Scanner chain/pasar ────CA───┘        (pipa process_ca yang sama, DB yang sama)
```

- **Listener (dari v12):** akun Telegram sendiri (userbot) mendengar semua
  grup/channel yang terdaftar di panel 📡 Sources; CA yang muncul diproses
  per pesan, dicocokkan ke source-nya secara live dari DB.
- **Scanner (dari v15):** tanpa menunggu siapa pun nge-call, bot memindai
  chain/pasar sendiri — event `PoolCreated` factory Uniswap v3 (semua pool
  baru) + profil/boost terbaru Dexscreener — menyaringnya lewat funnel
  pasar, lalu menyerahkan yang terbaik ke pipa yang sama dengan nama
  source sintetis **SCANNER**.

Token yang ditemukan kedua sumber hanya diproses **sekali** (dedupe global
per CA di tabel `calls`): siapa datang duluan yang tercatat sebagai
penemunya, kedatangan kedua berakhir sebagai `dup`. Ini terbukti di tes
logika arah dua-duanya (grup→scanner dan scanner→grup).

## Bedanya dengan versi sebelumnya

| Aspek | v12 | v14 (prototipe) | v15 | **v16 gabungan** |
|---|---|---|---|---|
| Listener grup (userbot) | ✅ | ✅ | ❌ dihapus | ✅ |
| Scanner chain/pasar | ❌ | ✅ terkunci keras | ✅ satu-satunya sumber | ✅ sumber kedua |
| Eksekusi scanner | — | MUSTAHIL (`allow_buy=False`) | mode global | **mode global** |
| Login userbot (QR) | wajib | wajib | tidak ada | **wajib lagi** |
| `validate()` wajibkan TG_API_ID/HASH | ya | ya | tidak | **ya** |
| Database | sendiri | sendiri | sendiri | **satu untuk dua sumber** |

Semantik eksekusi scanner di v16 mengikuti v15, BUKAN v14: scanner memanggil
`process_ca(..., allow_buy=True)`, dan yang mengatur beli/tidak adalah mode
global — terutama `agent_dry_run` (default **1/ON** → keputusan BUY berhenti
jadi notif 🧪 MODE LATIHAN berlabel sumbernya, mis. `_via SCANNER_`).
Mekanisme pagar `allow_buy=False` dari v14 masih ada di kode sebagai
pengaman untuk pemanggil yang memintanya, tapi scanner tidak memakainya.

## Alur start

```bash
pip install -r requirements.txt
cp .env.example .env   # isi nilainya (dari .env v12 yang sudah bekerja bisa disalin)
python bot.py login    # SEKALI: login akun Telegram via QR (userbot)
python bot.py run      # listener + scanner + engine + control bot jalan bersama
```

Saat start, terminal mencetak:
`[hoodsniper v16 GABUNGAN] running: listener grup Telegram + scanner chain/pasar dalam satu proses.`
dan loop scanner mencetak bannernya sendiri. Watermark factory pada jalan
pertama dilompatkan ke blok terbaru (tidak memindai riwayat dari genesis).

## Dua keluarga pengaturan

1. **Panel 📡 Sources** (listener): grup/channel + batas mcap per source.
   Source **SCANNER** muncul di data sebagai label asal-usul kandidat
   scanner (chat_id=0, bukan target listener; batas mcapnya diatur dari
   `scanner_mcap_max`, bukan dari panel Sources).
2. **Setting `scanner_*`** (Settings panel): `scanner_on` (default 1),
   `scanner_factory_on` (default 1), `scanner_interval_sec` (120),
   `scanner_mcap_max` (100000), `scanner_min_liq` (5000),
   `scanner_min_vol_h1` (1000), `scanner_min_txns_h1` (20),
   `scanner_max_age_min` (1440), `scanner_max_per_hour` (5).

⚠️ **Kuota LLM dipakai BERSAMA.** Kedua sumber menilai lewat Mata yang sama
dan meminum dari anggaran LLM yang sama (mis. kredit Kiro via 9Router).
`scanner_max_per_hour` membatasi HANYA keputusan dari scanner — call dari
grup tidak dibatasi cap itu. Scanner yang longgar di jam pasar ramai bisa
menghabiskan kuota yang seharusnya tersisa buat call grup.

## Membandingkan dua metode secara adil

Karena satu DB, perbandingannya apel-ke-apel: statistik per source
(📊 Stats / data `positions.source_name` & `calls.source_id`) menunjukkan
metode mana menemukan lebih banyak, mana yang lebih sering TP/SL, dalam
jendela waktu yang sama dan setting yang sama. Decision log (👣 Jejak)
juga tercampur dengan label sumber di setiap notif/keputusan (`_via ..._`).

## Pakem keamanan (tidak berubah)

- `agent_dry_run` default **ON**: selama latihan, BUY Mata hanya jadi notif.
- Dry-run itu **bukan** pagar global: ia hanya menahan keputusan BUY dari
  Mata. Bila LLM error/timeout, Screener jatuh ke rule-based yang bisa
  membeli beneran saat dry-run OFF — pagar latihan yang sesungguhnya tetap
  **wallet kosong**.
- Manager/Tangan (`agent_manage_on`) **tidak** mengecek dry-run dan bisa
  menjual posisi riel → biarkan **OFF** selama latihan.
- Wallet untuk `PRIVATE_KEY` tetap wallet burner khusus bot; key wallet
  utama (mis. BasedBot milik pemilik proyek) tidak boleh masuk `.env`.
- Satu token bot = satu proses. Jangan jalankan v16 bersamaan dengan
  v12/v14/v15 yang memakai BOT_TOKEN sama.

## Batasan yang diketahui

- Factory scanner hanya membaca pool **Uniswap v3** (instalasi ini memang
  v3-only sejak patch V2-opsional); pool v4 murni tidak tertangkap factory.
- Profil/boost Dexscreener adalah **sampel** (token yang baru update
  profil/di-boost), bukan daftar semua coin — tulang punggung "semua pool
  baru" adalah event factory.
- Cap per jam scanner bersifat in-process: restart bot mengosongkannya.
- Kandidat scanner tidak punya konteks caller (tidak ada manusia yang
  nge-call), jadi penilaian Mata murni dari data pasar + safety.

## Berkas penting di folder ini

- `sniper/scanner.py` — modul scanner (semantik gabungan).
- `test_scanner_logic.py` — 40 tes offline: logika scanner + aturan mode +
  **dedupe lintas-sumber dua arah** + validasi startup gabungan.
- `test_scanner.py` — satu siklus penemuan read-only (jaringan nyata).
- `SCANNER_PROTOTYPE.md` — dokumen desain prototipe v14 (HISTORIS; jaminan
  dry-run kerasnya tidak berlaku lagi di v16 — yang berlaku dokumen ini).
