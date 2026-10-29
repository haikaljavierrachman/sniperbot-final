"""Safety filter pre-buy: dev holding & konsentrasi top holders.

Sumber data: Blockscout Robinhood Chain (deployer & daftar holder) + RPC
(balanceOf/totalSupply). Sync — panggil via asyncio.to_thread.
"""
import functools
try:
    from curl_cffi import requests as _requests
    _S = _requests.Session(impersonate="chrome120")
except ImportError:
    import requests as _requests
    _S = _requests.Session()
    _S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
    print("[safety] ⚠️ PERINGATAN: curl_cffi belum terpasang! Query explorer Blockscout akan diblokir Cloudflare 403 (fail-closed). Jalankan: pip install curl_cffi")
from web3 import Web3
from . import config, db

DEAD = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}

TOTAL_SUPPLY_ABI = [{
    "name": "totalSupply", "type": "function", "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "uint256"}],
}]


def _get(path, params=None):
    r = _S.get(f"{config.BLOCKSCOUT_API}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def creator_of(ca: str, cache: bool = True):
    """Alamat deployer ASLI kontrak token (EOA pembuat creation tx).

    Jangan pakai /addresses/{ca}.creator_address_hash — untuk token
    launchpad (Pons) itu adalah KONTRAK ROUTER/LAUNCHER, bukan deployer
    manusia (terbukti probe Marvin #1..#3: ketiganya menunjuk ke router
    0x1cbaF24D...). Jalur benar (terverifikasi):
      1. /addresses/{ca}             -> creation_transaction_hash
      2. /transactions/{creation_tx} -> from = EOA deployer

    Hasil dicache ke tabel deployer_registry (clone_guard) supaya tidak
    membanjiri Blockscout tiap kali dipanggil. None kalau tidak ketahuan.
    """
    import time as _t
    ca = (ca or "").strip()
    if not ca.lower().startswith("0x"):
        return None
    ca = ca.lower()
    # ---- cache lookup (fresh < 14 hari) ----
    if cache:
        try:
            from . import clone_guard as _cg
            row = _cg.get_registry(ca)
            if row and row.get("deployer"):
                if _t.time() - (row.get("ts") or 0) < 14 * 86400:
                    return row["deployer"]
        except Exception:
            pass
    try:
        d = _get(f"/addresses/{ca}")
        creation_tx = (d or {}).get("creation_transaction_hash")
        if not creation_tx:
            return None
        tx = _get(f"/transactions/{creation_tx}")
        dev = ((tx or {}).get("from") or {}).get("hash")
        dev = dev.lower() if dev else None
        # ---- isi cache (best-effort, tanpa fetch tambahan) ----
        if cache and dev:
            try:
                from . import clone_guard as _cg
                with db._lock, db._conn() as c:
                    _cg._init_tables(c)
                    c.execute("""INSERT INTO deployer_registry
                        (ca,deployer,template_fp,symbol,name,total_supply,funders,status,reason,ts)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(ca) DO UPDATE SET
                            deployer = COALESCE(excluded.deployer, deployer),
                            ts = CASE WHEN excluded.deployer IS NOT NULL
                                      THEN excluded.ts ELSE ts END""",
                        (ca, dev, None, "", "", "", "", "ok", "", int(_t.time())))
            except Exception:
                pass
        return dev
    except Exception:
        return None


def _total_supply(trader, ca: str) -> int:
    c = trader.w3.eth.contract(Web3.to_checksum_address(ca), abi=TOTAL_SUPPLY_ABI)
    return c.functions.totalSupply().call()


def dev_pct(trader, ca: str) -> float:
    """% supply yang dipegang deployer. 0 kalau deployer tidak ketemu."""
    dev = creator_of(ca)
    if not dev or dev.lower() in DEAD:
        return 0.0
    ts = _total_supply(trader, ca)
    if ts <= 0:
        return 0.0
    bal = trader.erc20(ca).functions.balanceOf(Web3.to_checksum_address(dev)).call()
    return bal / ts * 100


def top_holders_pct(trader, ca: str, exclude: set, n: int = 10) -> float:
    """% supply gabungan n holder teratas, exclude pool/burn/dev sudah dihitung terpisah."""
    try:
        d = _get(f"/tokens/{ca}/holders")
    except Exception:
        return 0.0  # data holder belum tersedia (token terlalu baru) — jangan blokir
    ts = _total_supply(trader, ca)
    if ts <= 0:
        return 0.0
    ex = {a.lower() for a in exclude} | DEAD
    vals = []
    for item in d.get("items") or []:
        addr = ((item.get("address") or {}).get("hash") or "").lower()
        if addr in ex:
            continue
        try:
            vals.append(int(item.get("value") or 0))
        except (TypeError, ValueError):
            continue
        if len(vals) >= n:
            break
    return sum(vals) / ts * 100


# =========================================================
#  AUDIT KONTRAK (anti-honeypot & anti-blacklist)
# =========================================================

# 4-byte selector fungsi berbahaya (blacklist/freeze/trading-lock/fee tax)
_DANGER_SIGNATURES = (
    # blacklist & freeze
    "blacklist(address)",
    "setBlacklist(address,bool)",
    "isBlacklisted(address)",
    "freezeAccount(address,bool)",
    "addBlacklist(address)",
    "setBot(address,bool)",
    # trading lock / honeypot
    "setTradingEnabled(bool)",
    "enableTrading()",
    "disableTrading()",
    "pause()",
    "setPause(bool)",
    # fee manipulator
    "setSellFee(uint256)",
    "setTaxFee(uint256)",
    "setFee(uint256,uint256)",
)


@functools.lru_cache(maxsize=None)
def _selector(sig: str) -> str:
    """Hex 4-byte selector keccak256(sig) — yaitu 4 byte pertama kanonikal."""
    return Web3.keccak(text=sig)[:4].hex().lower()


def audit_contract(trader, ca: str) -> tuple[bool, str]:
    """Bedah kode kontrak sebelum beli.

    Lapis A: bytecode on-chain via RPC (~instan) — token HONEYPOT tanpa
             source terverifikasi pun ketangkep.
    Lapis B: ABI koin terverifikasi via Blockscout (timeout ketat 3.5s),
             fallback diam ke hasil lapis A kalau timeout/error/unverified.

    Return (lolos, alasan).
    """
    # ---- A. bytecode on-chain ----
    try:
        code = trader.w3.eth.get_code(Web3.to_checksum_address(ca)).hex().lower()
    except Exception as e:
        return False, f"gagal ambil bytecode ({e.__class__.__name__})"
    if not code or len(code) - 2 <= 4:  # "0x" atau << 4 hex char
        return False, "bukan kontrak (bytecode kosong)"

    sels = [(sig, _selector(sig)) for sig in _DANGER_SIGNATURES]
    detected = [sig for sig, sel in sels if sel in code]
    if detected:
        return False, f"kontrak memiliki signature berbahaya ({detected[0]})"

    # ---- B. ABI source terverifikasi via Blockscout ----
    try:
        r = _S.get(f"{config.BLOCKSCOUT_API}/smart-contracts/{ca}", timeout=3.5)
        r.raise_for_status()
        sc = r.json()
        abi = sc.get("abi")
        if not isinstance(abi, list):
            return True, "bytecode clean (source tidak terverifikasi)"
        keywords = ("blacklist", "freeze", "locktrading", "pausetrading",
                    "settaxfee", "setsellfee", "tradingstatus")
        for item in abi:
            if not isinstance(item, dict):
                continue
            names = {str(item.get("name") or "").lower()}
            for inp in item.get("inputs") or []:
                if isinstance(inp, dict) and inp.get("name"):
                    names.add(str(inp["name"]).lower())
            for nm in names:
                for kw in keywords:
                    if kw in nm:
                        return False, f"kontrak terverifikasi memuat fungsi berbahaya '{item.get('name')}'"
        return True, "bytecode clean · abi clean"
    except Exception:
        # timeout / error / unverified → fallback ke hasil lapis A (clean)
        return True, "bytecode clean (Blockscout tidak terjangkau)"


def check(trader, ca: str, pair) -> tuple[bool, str]:
    """Return (lolos, alasan). Dipanggil sebelum buy."""
    if db.get("safety_on", str) != "1":
        return True, "safety off"

    # ---- audit kode kontrak (anti-honeypot / anti-blacklist) ----
    audit_ok, audit_msg = audit_contract(trader, ca)
    if not audit_ok:
        return False, f"audit kontrak gagal: {audit_msg}"
    audit_note = audit_msg.split("(", 1)[0].strip()  # ringkas untuk catatan sukses

    # ---- 🛡️ clone / operator-family guard (v16.6) ----
    # SETELAH audit kontrak, SEBELUM cek dev/top10.
    # Guard ini WAJIB nol-latency: `is_clone_risk()` 100% DB-only, sedangkan
    # pengisian data on-chain (fingerprint/deployer/funder) dijalankan
    # `register_async()` di background. Kalau dipaksa sinkron, entry tertunda
    # 18-32 detik (terukur) dan koin sudah kabur.
    try:
        from . import clone_guard as _cg
        _bt = (pair or {}).get("baseToken") or {}
        _sym = _bt.get("symbol") or ""
        _nm = _bt.get("name") or ""
        clone_risk, clone_reason = _cg.is_clone_risk(ca, symbol=_sym, name=_nm)
        if clone_risk:
            return False, f"clone guard: {clone_reason}"
        _cg.register_async(ca, symbol=_sym, name=_nm)
    except Exception as e:
        print(f"[safety] ⚠️ clone_guard gagal dijalankan (fail-open): "
              f"{e.__class__.__name__}: {e}")

    # ---- 🚦 Entry rate-limit per operator-family (v16.6 ratelimit) ----
    # Batas JUMLAH entry per keluarga (bukan blacklist token) — fail-open.
    # (`_sym`/`_nm` sudah selalu terisi oleh blok clone guard di atas —
    #  aturannya langsung print warning dan lanjut kalau blok itu gagal.)
    try:
        from . import clone_guard as _cg2
        limit_hits = int(db.get("clone_max_entries_per_day") or 3)
        window_h = int(db.get("clone_entry_window_hours") or 24)
        if limit_hits > 0:
            too_many, why = _cg2.too_many_entries(
                ca, symbol=_sym, name=_nm,
                limit=limit_hits, hours=window_h)
            if too_many:
                return False, f"family limit: {why}"
    except Exception as e:
        print(f"[safety] ⚠️ entry rate-limit gagal dijalankan (fail-open): "
              f"{e.__class__.__name__}: {e}")

    max_dev = db.get("dev_max_pct")
    max_top = db.get("top10_max_pct")

    # fail_closed (default 1): kalau data holder/dev TIDAK bisa diverifikasi
    # (Blockscout error/timeout) → SKIP, jangan lolos. Set 0 utk perilaku
    # agresif lama (lolos kalau API ngadat).
    fail_closed = db.get("safety_fail_closed", str) == "1"

    # ---- dev holding ----
    dev_failed = False
    try:
        dp = dev_pct(trader, ca)
    except Exception:
        dp, dev_failed = -1.0, True
    if not dev_failed and dp > max_dev:
        return False, f"dev pegang {dp:.1f}% supply (max {max_dev:.0f}%)"

    # ---- top-10 concentration ----
    top_failed = False
    exclude = {pair.get("pairAddress", ""), ca}
    try:
        dev = creator_of(ca)
        if dev:
            exclude.add(dev)
    except Exception:
        pass
    try:
        tp = top_holders_pct(trader, ca, exclude)
        if tp <= 0:
            top_failed = True  # 0% = data holder belum ke-index / kosong, bukan "aman"
    except Exception:
        tp, top_failed = -1.0, True
    if not top_failed and tp > max_top:
        return False, f"top10 holders pegang {tp:.1f}% (max {max_top:.0f}%)"

    # ---- kebijakan saat verifikasi gagal ----
    if dev_failed or top_failed:
        which = []
        if dev_failed: which.append("dev")
        if top_failed: which.append("top10")
        w = "+".join(which)
        if fail_closed:
            return False, f"data {w} tidak bisa diverifikasi (Blockscout) — SKIP (fail-closed)"
        return True, f"⚠️ {w} tidak terverifikasi, lolos (fail-open)"

    return True, f"dev {dp:.1f}% · top10 {tp:.1f}% · {audit_note}"
