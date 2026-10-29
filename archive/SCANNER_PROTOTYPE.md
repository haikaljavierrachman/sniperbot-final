> ⚠️ **DOKUMEN HISTORIS (prototipe v14).** Di folder ini (**v16 GABUNGAN**)
> jaminan "dry-run keras" yang dijelaskan di bawah **TIDAK berlaku lagi**:
> scanner sekarang berjalan berdampingan dengan listener grup dalam satu
> proses, memanggil `process_ca(allow_buy=True)`, dan eksekusinya diatur
> mode global (`agent_dry_run`, default ON) persis seperti sinyal grup.
> Semantik yang berlaku: baca **README_GABUNGAN.md**. Dokumen ini disimpan
> apa adanya sebagai catatan desain prototipe.

# 🔭 Scanner v14 — Prototipe Metode "Analisa Semua Coin"

Dokumen ini menjelaskan folder `hoodsniper_v14_scanner`: salinan hoodsniper
**v12** + patch V2-opsional + patch SSE (keduanya sudah teruji sebelumnya) +
satu kemampuan baru — **scanner**: bot menemukan kandidat token dari pasar
langsung, bukan hanya menunggu CA di-call di grup Telegram.

**Status: PROTOTIPE DRY-RUN KERAS.** Jalur scanner secara desain TIDAK BISA
membeli (penjelasan di bagian "Jaminan dry-run" di bawah). Jangan dipakai
live sebelum masa observasi dan keputusan pemilik proyek.

## Metode scanner itu apa

Listener lama bersifat pasif: ada yang nge-call CA di grup → bot menilai →
beli/tidak. Scanner membalik arahnya:

```
SUMBER KANDIDAT                          FUNNEL                        PIPA LAMA
─────────────────────────────            ──────────────────────        ──────────────────
Profil/boost terbaru Dexscreener  ──┐
                                    ├─▶  buang yang sudah pernah  ─▶  process_ca():
Event PoolCreated factory v3        │    dilihat (seen_call)            filter mcap →
(opsional, scanner_factory_on)    ──┘    filter pasar MURAH:            sosmed → safety →
                                         liq, umur, volume, txns        Mata (LLM)
                                         peringkat skor                 │
                                         cap keputusan per jam          ▼
                                                              ⛔ STOP — buy TIDAK
                                                                 dieksekusi
                                                                 (no_buy_scanner)
```

Yang dinilai Mata dari scanner tidak punya "caller" — tidak ada konteks
track record pemanggil seperti call grup. Penilaiannya murni data token +
rekam jejak source sintetis bernama `SCANNER`.

## Semua setting `scanner_*`

Diatur dari panel ⚙️ Settings control bot (label berawalan 🔭) atau langsung
di tabel settings DB. Default = scanner MATI.

| Setting | Default | Arti |
|---|---|---|
| `scanner_on` | `0` | Saklar utama loop scanner (`1` = jalan). |
| `scanner_interval_sec` | `120` | Jeda antar siklus pemindaian (detik, min 30). |
| `scanner_mcap_max` | `100000` | Batas mcap (USD) untuk source SCANNER — kandidat di atas ini disaring process_ca seperti source grup biasa. |
| `scanner_min_liq` | `5000` | Likuiditas minimum (USD) di filter pasar. |
| `scanner_min_vol_h1` | `1000` | Volume 1 jam minimum (USD) di filter pasar. |
| `scanner_min_txns_h1` | `20` | Jumlah transaksi (buy+sell) 1 jam minimum di filter pasar. |
| `scanner_max_age_min` | `1440` | Umur pair maksimum (menit). `0` = abaikan umur. |
| `scanner_max_per_hour` | `5` | Cap keputusan yang diproses per jam (pagar biaya LLM). In-process: restart bot = hitungan mulai dari nol lagi. |
| `scanner_factory_on` | `0` | `1` = aktifkan sumber event `PoolCreated` factory Uniswap v3 via RPC. |
| `scanner_last_block` | `0` | Watermark blok factory (state internal, jangan diubah manual). |

Di luar itu scanner tunduk pada setting lama yang sama dengan listener:
`min_liq_usd`, filter sosmed, safety (dev/top10), `agent_on`,
`agent_min_conviction`, `paused`, dll.

## Jaminan dry-run: bagaimana persisnya ditegakkan

1. Inti pemrosesan per-CA di `sniper/listener.py` sekarang adalah
   `process_ca(ca, src, caller_id, caller_name, allow_buy=True)`.
2. Di dalamnya, tepat SEBELUM satu-satunya titik yang bisa mencapai
   `trader.buy`, ada pagar: kalau `allow_buy=False` → kirim notif
   `🔭 SCANNER (dry)` dan return `"no_buy_scanner"`.
3. Pagar ini ditaruh sesudah semua keputusan Mata (BUY/SKIP/ragu/dry-run),
   sehingga menutup juga jalur **fallback rule-based** (Mata mati/gagal →
   decision `None`) — jalur yang di v12 bisa membeli beneran.
4. `scanner_loop` **selalu** memanggil `process_ca(..., allow_buy=False)`.
   Tidak ada setting yang diteruskan ke parameter ini. Mengubah
   `agent_dry_run` ke `0` pun tidak membuka pagar ini (loop hanya mencetak
   peringatan).
5. Dibuktikan otomatis di `test_scanner_logic.py`: setup identik dengan Mata
   ON + dry-run OFF menghasilkan `buy_calls == 0` untuk `allow_buy=False`,
   dan `buy_calls == 1` untuk kontrol `allow_buy=True`.

## Cara mencoba

**A. Tanpa bot, tanpa Telegram (paling aman, disarankan pertama kali):**

```bash
python test_scanner.py            # satu siklus: kandidat → funnel → top-10
python test_scanner.py --factory  # + kandidat event factory v3 dari RPC
python test_scanner_logic.py      # 19 tes logika offline (pagar, filter, cap)
```

`test_scanner.py` read-only terhadap pasar: tidak login, tidak membuat
Trader, tidak memanggil LLM, tidak memanggil process_ca.

**B. Di bot sungguhan (`python bot.py run`):**

1. Pastikan listener sudah ter-register normal (mode run biasa, BUKAN
   panel-only — di mode panel `run_panel.py` loop scanner hanya mencetak
   "listener belum ter-register" dan melewati siklus, karena
   `listener._trader/_notify` belum terpasang).
2. Dari panel control bot: ⚙️ Settings → set `scanner_on` = `1`.
3. Amati notif `🔭 SCANNER (dry)` dan log `[scanner] kandidat=… lolos=…
   diproses=…`. Matikan lagi dengan `scanner_on` = `0`.
4. Opsional: `scanner_factory_on` = `1` untuk sumber event pool baru.

## Batasan yang diketahui (jujur)

- **Profil/boost Dexscreener adalah SAMPEL, bukan firehose.** Isinya token
  yang baru memperbarui profil atau sedang di-boost — bukan "semua coin".
  Sumber factory menutup sebagian celah ini (pool v3 baru), tapi tetap:
- **Factory = Uniswap v3 saja.** Pool v2/v4 dan token yang masih di
  launchpad (belum punya pool v3) tidak tertangkap sumber ini.
- **Cap per jam in-memory** — restart proses mengosongkan hitungan 1 jam.
- **Tanpa konteks caller.** Salah satu sinyal kualitas di metode grup
  (rekam jejak pemanggil) tidak ada di metode scanner.
- Skor peringkat (`score_pair`) adalah heuristik prototipe:
  `log10(1+vol_h1) + log10(1+liq) + rasio_beli_h1`. Belum divalidasi data.
- Kandidat yang gugur di funnel pasar TIDAK dicatat ke tabel calls, jadi
  siklus berikutnya mereka dinilai ulang dari data segar (disengaja:
  token yang likuiditasnya baru tumbuh harus bisa lolos belakangan).
  Yang sudah masuk `process_ca` tercatat via `record_call` seperti biasa.

## Sebelum boleh live (belum dikerjakan, butuh keputusan)

1. Masa observasi dry-run: bandingkan keputusan scanner vs call grup
   (decision log role screener atas nama source SCANNER).
2. Keputusan pemilik proyek soal metode ini menggantikan/melengkapi grup.
3. Kalau suatu hari scanner boleh membeli: itu perubahan desain terpisah —
   parameter `allow_buy` harus disambungkan ke setting baru yang eksplisit,
   ditambah pagar wallet kosong & Tangan OFF seperti fase latihan lain.
   Selama dokumen ini belum diganti, anggap scanner TIDAK AKAN PERNAH live.

## Beda vs v12 (file per file)

| File | Perubahan |
|---|---|
| `sniper/config.py` | (a) Patch V2: `V2_ROUTER` tidak lagi wajib di `validate()`. (b) +10 setting `scanner_*` di `SETTING_DEFAULTS` dan 9 label 🔭 di `SETTING_LABELS` (`scanner_last_block` internal, tanpa label). |
| `sniper/trader.py` | Patch V2 saja: router v2 hanya dibuat/divalidasi kalau `V2_ROUTER` terisi; pool v2 tanpa router → error jelas "mode v3-only". |
| `sniper/agent.py` | Patch SSE saja: payload `"stream": False` + helper `_openai_text()` merangkai respons SSE gateway (9Router/Kiro) agar tidak JSONDecodeError. |
| `sniper/listener.py` | Refactor TANPA ubah perilaku: badan per-CA dari `_handle` diekstrak menjadi `process_ca(ca, src, caller_id, caller_name, allow_buy=True)` yang me-return status string; `continue` → `return <status>`; pagar baru `allow_buy=False` → notif SCANNER (dry) + `"no_buy_scanner"` sebelum titik buy. `_handle` kini memanggil `process_ca` per CA (allow_buy default True = perilaku lama persis). |
| `sniper/scanner.py` | **BARU.** Sumber kandidat (profil/boost + factory), `market_ok`, `score_pair`, `collect_and_rank`, cap per jam, source SCANNER, `scanner_loop`. |
| `bot.py` | +import `scanner`, +1 baris `asyncio.create_task(scanner.scanner_loop())` setelah loop engine. Tidak ada restrukturisasi. |
| `test_scanner.py` | **BARU.** Tes standalone satu siklus (read-only, tanpa Telegram/Trader/LLM/process_ca). |
| `test_scanner_logic.py` | **BARU.** 19 tes logika offline: parse log factory, market_ok, skor, cap, dan bukti pagar keras vs kontrol. |
| `SCANNER_PROTOTYPE.md` | **BARU.** Dokumen ini. |

File lain (`db.py`, `engine.py`, `safety.py`, `dexscreener.py`,
`control_bot.py`, dll) TIDAK berubah dari v12 + patch di atas.
Backup patch ikut tersalin: `sniper/*.bak-v2patch`, `sniper/agent.py.bak-ssepatch`.
