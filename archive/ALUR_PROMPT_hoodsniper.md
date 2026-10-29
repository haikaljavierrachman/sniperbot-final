# ALUR PROMPT — hoodsniper (multi-channel degen sniper + filter sosmed)

> Prompt ini dipakai buat instruksiin coding-agent supaya **melanjutkan base `hoodsniper__11_`**, bukan bikin dari nol.
> Base sudah punya: Telethon QR login, multi-source tanpa batas, auto-buy Robinhood Chain, safety filter, control bot.
> Tugas agent: pastiin alur 2 channel degen jalan mulus + **tambahkan Filter Sosmed dengan opsi**.

---

## 0. PERAN & KONTRAK (paste ini ke agent)

Kamu adalah engineer bot trading. Lanjutkan project Python `hoodsniper` (repo terlampir). JANGAN rombak arsitektur — ikuti pola modul yang sudah ada (`sniper/config.py`, `db.py`, `listener.py`, `dexscreener.py`, `safety.py`, `trader.py`, `engine.py`, `control_bot.py`, `agent.py`; entrypoint `bot.py`).

Aturan main:
- Tambah fitur lewat **setting di DB** (pola `SETTING_DEFAULTS` + `SETTING_LABELS`), bukan hardcode.
- Semua toggle bisa diatur runtime via control bot (BotFather), tanpa restart.
- Panggilan network sync (requests/web3) selalu lewat `asyncio.to_thread(...)`.
- Kalau data gagal diambil, ikuti kebijakan **fail-closed** yang sudah ada (skip, jangan asal beli).
- Output selesai = kode jalan + update `PANDUAN.md` + `CARAUPDATE.md`.

---

## 1. ALUR RUNTIME (yang harus terjadi, urut)

```
[VPS]
  └─ akun Telegram (userbot Telethon, QR login) — cuma buat PANTAU, gak buat beli
        └─ subscribe/join 2 channel degen (atau lebih, tanpa batas)
              │
              ▼ ada pesan baru
        listener.handler
              ├─ match_source(chat_id, topic_id)  → skip kalau bukan source terdaftar
              ├─ blacklist caller? → skip
              ├─ ekstrak CA (regex 0x[a-f0-9]{40})
              ├─ seen_call(CA)? → skip (anti-dobel)
              ├─ dexscreener.best_pair(CA) → gak ada di Robinhood Chain? skip
              ├─ FILTER mcap (per-source mcap_max)
              ├─ FILTER min_liq_usd
              ├─ FILTER SOSMED  ← ★ FITUR BARU
              ├─ safety.check (dev% / top10%)  → fail-closed
              ├─ (opsional) agent Mata: BUY/SKIP + conviction
              ▼
        trader.buy(CA)  ← pakai bot sendiri, wallet agent, router Uniswap Robinhood Chain
              ▼
        db.open_position + notif Telegram (tombol Sell 50/ALL, Chart, Tx)
              ▼
[engine.monitor_loop] pantau TP1/TP2/SL/trailing/rug → auto-sell + notif
```

Beli **hanya** dieksekusi oleh bot (wallet agent). Akun Telegram userbot murni sensor sinyal — jangan pernah taruh private key di sesi userbot.

---

## 2. SETUP 2 CHANNEL DEGEN

Tujuan: userbot mantau **2 channel early**, tiap channel punya limit mcap sendiri.

1. Akun userbot harus **join dulu** ke-2 channel (via HP/app), baru session Telethon bisa baca pesannya.
2. Daftarkan 2 source lewat panel bot → tombol **➕ Tambah Source**. Format sudah ada di `control_bot._do_add_source`:
   ```
   <link_topic_atau_chat_id> <mcap_max> <nama>
   ```
   - Channel biasa (bukan forum topik) → cukup `chat_id` atau link `t.me/...`, `topic` = 0 (semua pesan).
   - Kalau channel pakai topik (forum), pakai link topik biar `topic_id` ke-parse.
   - Contoh:
     - `-1001111111111 30000 Degen Channel A`
     - `-1002222222222 10000 Degen Channel B`
3. `db.match_source` sudah handle `topic_id=0 = semua topik`, jadi channel non-forum aman.
4. **Tanpa batas**: mau nambah channel ke-3, ke-10, tinggal ➕ Tambah Source lagi. `sources` table gak ada limit.

> Catatan `chat_id`: buat channel/grup pakai format `-100xxxxxxxxxx`. Kalau agent perlu resolve dari username, tambahkan helper `client.get_entity(link)` di flow add-source (opsional, boleh disaranin tapi jangan bikin wajib).

---

## 3. ★ FITUR BARU — FILTER SOSMED (dengan opsi)

Tujuan: naikin win-rate dengan cuma masuk ke token yang **punya jejak sosmed** (website / twitter / telegram). Harus **ada pilihannya** (bisa dimatiin, bisa diperketat).

### 3.1 Sumber data
Dexscreener pair object punya field `info`:
```json
"info": {
  "websites": [{"label": "Website", "url": "https://..."}],
  "socials":  [{"type": "twitter", "url": "https://x.com/..."},
               {"type": "telegram", "url": "https://t.me/..."}]
}
```
`type` yang umum: `twitter`, `telegram`, `discord`, `website`. Kadang `info` kosong / gak ada untuk token super baru.

### 3.2 Tambah helper di `sniper/dexscreener.py`
```python
def socials_of(pair) -> dict:
    """Ringkas jejak sosmed dari Dexscreener info.
    Return: {"has_web": bool, "has_twitter": bool, "has_telegram": bool,
             "types": set[str], "count": int}"""
    info = pair.get("info") or {}
    webs = info.get("websites") or []
    socs = info.get("socials") or []
    types = {(s.get("type") or "").lower() for s in socs if s.get("url")}
    has_web = bool([w for w in webs if w.get("url")]) or ("website" in types)
    return {
        "has_web": has_web,
        "has_twitter": "twitter" in types or "x" in types,
        "has_telegram": "telegram" in types,
        "types": types,
        "count": len([w for w in webs if w.get("url")]) + len([s for s in socs if s.get("url")]),
    }
```

### 3.3 Setting baru (masukin ke `config.SETTING_DEFAULTS` + `SETTING_LABELS`)
```python
# di SETTING_DEFAULTS
"social_filter":     "any",   # mode filter sosmed
"social_min_count":  "1",     # min jumlah link sosmed (dipakai mode 'count')
"social_fail_closed":"0",     # 1 = kalau info Dexscreener gagal/kosong → SKIP

# di SETTING_LABELS
"social_filter":     "🌐 Filter sosmed (off/any/tw/tw_tg/web/count)",
"social_min_count":  "🌐 Min jumlah sosmed (mode count)",
"social_fail_closed":"🌐 Skip kalau info sosmed kosong (1/0)",
```

**Mode `social_filter` (ada pilihannya):**
| Mode      | Lolos kalau...                                        |
|-----------|------------------------------------------------------|
| `off`     | filter mati, semua lolos                             |
| `any`     | ada minimal 1 sosmed apa aja (web/tw/tg/dll)        |
| `tw`      | punya Twitter/X                                      |
| `tw_tg`   | punya Twitter/X **dan** Telegram                     |
| `web`     | punya website                                        |
| `count`   | jumlah link sosmed ≥ `social_min_count`             |

### 3.4 Fungsi filter (bikin di `dexscreener.py` atau modul kecil `social.py`)
```python
def social_ok(pair) -> tuple[bool, str]:
    from . import db
    mode = db.get("social_filter", str)
    if mode == "off":
        return True, "social off"
    s = socials_of(pair)
    if s["count"] == 0:
        # info kosong (token terlalu baru / Dexscreener belum index)
        if db.get("social_fail_closed", str) == "1":
            return False, "belum ada jejak sosmed (fail-closed)"
        return True, "⚠️ info sosmed kosong, lolos (fail-open)"
    label = "·".join(sorted(s["types"])) or ("web" if s["has_web"] else "?")
    if mode == "any":
        return (True, f"sosmed: {label}") if s["count"] > 0 else (False, "gak ada sosmed")
    if mode == "tw":
        return (s["has_twitter"], "punya Twitter" if s["has_twitter"] else "gak ada Twitter")
    if mode == "tw_tg":
        ok = s["has_twitter"] and s["has_telegram"]
        return (ok, "Twitter+TG" if ok else "kurang (butuh Twitter & TG)")
    if mode == "web":
        return (s["has_web"], "punya website" if s["has_web"] else "gak ada website")
    if mode == "count":
        need = int(db.get("social_min_count"))
        ok = s["count"] >= need
        return (ok, f"{s['count']} link sosmed (min {need})")
    return True, "mode tak dikenal → lolos"
```

### 3.5 Integrasi di `listener._handle`
Sisipkan **setelah** cek `min_liq` dan **sebelum** `safety.check` (murah dulu, mahal belakangan):
```python
if liq < db.get("min_liq_usd"):
    print(f"[skip] {sym} liq rendah"); continue

# ★ filter sosmed
soc_ok, soc_reason = dx.social_ok(pair)
if not soc_ok:
    print(f"[social-skip] {sym}: {soc_reason}")
    await _notify(f"🌐 **SKIP {sym}** _via {src['name']}_\n`{ca}`\n{soc_reason}")
    continue

ok, reason = await asyncio.to_thread(safety.check, _trader, ca, pair)
...
```
Lalu simpan `soc_reason` biar bisa ditempel di notif BUY (contoh: tambah baris `🌐 {soc_reason}`).

### 3.6 Panel control (biar bisa "pilih" dari HP)
`social_filter` itu enum, jadi jangan cuma input teks bebas — kasih tombol pilihan. Tambah handler di `control_bot`:
- Di menu Settings, saat user tap `set:social_filter`, munculin baris tombol:
  ```python
  buttons = [[Button.inline("Off","setval:social_filter:off"),
              Button.inline("Any","setval:social_filter:any"),
              Button.inline("TW","setval:social_filter:tw")],
             [Button.inline("TW+TG","setval:social_filter:tw_tg"),
              Button.inline("Web","setval:social_filter:web"),
              Button.inline("Count","setval:social_filter:count")],
             [Button.inline("« Settings", b"settings")]]
  ```
- Tambah callback `setval:<key>:<val>` → `db.set_(key, val)` → konfirmasi.
- `social_min_count` & `social_fail_closed` cukup pakai input angka lewat pola `set:key` yang sudah ada.

### 3.7 Default rekomendasi (santai tapi aman)
- `social_filter=any`, `social_fail_closed=0` → buat degen early, banyak token bagus baru punya TG doang di menit awal; `any` gak kelewat ketat.
- Kalau mau lebih selektif (win-rate > jumlah entry): `social_filter=tw` atau `tw_tg`.

---

## 4. ENV (.env) — yang wajib diisi

```env
# Chain Robinhood
RPC_URL=https://rpc.mainnet.chain.robinhood.com
CHAIN_ID=4663
PRIVATE_KEY=0x...            # wallet agent (khusus bot, isi kecil aja)
SWAP_ROUTER02=0x...          # Uniswap v3 router di Robinhood Chain
V2_ROUTER=0x...              # Uniswap v2 router

# Telegram userbot (Telethon, QR)
TG_API_ID=123456
TG_API_HASH=xxxxxxxx
SESSION_NAME=hoodsniper

# Control bot (BotFather)
BOT_TOKEN=123:ABC
ADMIN_ID=123456789           # user id Kakak (cek @userinfobot)

# LLM (opsional, buat agent Mata/Tangan)
LLM_PROTOCOL=anthropic
LLM_API_KEY=sk-...
```

---

## 5. DEPLOY DI VPS (urut)

```bash
# 1. install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. login userbot (sekali, interaktif — scan QR dari HP)
python bot.py login
#    HP: Settings → Devices → Link Desktop Device → scan QR di terminal

# 3. jalankan pakai PM2 (auto-restart, jalan pas lo tidur)
pm2 start ecosystem.config.js
pm2 save && pm2 startup
```
Setelah `run`, bot kirim menu ke `ADMIN_ID`. Tambah 2 channel degen via ➕ Tambah Source, set `social_filter`, selesai.

---

## 6. ACCEPTANCE CHECKLIST (agent harus pastiin semua ✅)

- [ ] Userbot join 2 channel degen, `match_source` kena buat dua-duanya (log `[..]` muncul saat ada CA).
- [ ] Tambah source ke-3 via panel → langsung kepantau tanpa restart.
- [ ] CA dobel dalam waktu dekat → cuma diproses sekali (`seen_call`).
- [ ] `social_filter=off` → token tanpa sosmed tetap lolos.
- [ ] `social_filter=tw` → token yang cuma punya TG **ke-skip**, ada notif `🌐 SKIP`.
- [ ] `social_filter=count`, `social_min_count=2` → token 1 link ke-skip, 2 link lolos.
- [ ] `social_fail_closed=1` + token tanpa `info` → **skip** (bukan asal beli).
- [ ] Ganti mode filter dari panel (tombol) langsung kepakai di call berikutnya.
- [ ] Notif BUY nampilin baris jejak sosmed.
- [ ] Beli tetap dieksekusi bot/wallet agent, userbot gak pernah pegang private key.
- [ ] `PANDUAN.md` + `CARAUPDATE.md` di-update sesuai fitur baru.

---

## 7. CATATAN PENTING

- Filter sosmed **bukan jaminan aman** — cuma nurunin peluang scam murni; safety filter (dev%/top10%) tetap wajib nyala.
- Token super early kadang belum ke-index `info`-nya di Dexscreener walau sosmednya ada di pesan channel. Kalau mau agresif di menit-0, pakai `any` + `fail_closed=0`. Kalau mau bersih, `tw`/`tw_tg` + `fail_closed=1` (trade-off: entry lebih dikit).
- Semua angka PnL tetep pakai realized asli dari wallet (jangan balik ke estimasi layar).
