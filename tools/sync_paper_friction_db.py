import sqlite3
conn = sqlite3.connect("hoodsniper.db")
c = conn.cursor()
print(c.execute("SELECT key,value FROM settings WHERE key LIKE 'paper_%'").fetchall())
new = {"paper_gas_eth": "0.0001", "paper_slippage_pct": "0.02", "paper_entry_spread_pct": "0.025"}
for k, v in new.items():
    if c.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
        c.execute("UPDATE settings SET value=? WHERE key=?", (v, k))
    else:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, v))
conn.commit()
print(c.execute("SELECT key,value FROM settings WHERE key LIKE 'paper_%'").fetchall())
conn.close()
