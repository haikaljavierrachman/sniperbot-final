"""Maintenance script (v16.7): tampilkan status DB hoodsniper.db (read-only)."""
import sqlite3

c = sqlite3.connect("hoodsniper.db")
c.row_factory = sqlite3.Row

print("settings:")
for r in c.execute("SELECT key,value FROM settings WHERE key IN "
                   "('poll_seconds','anti_rug_poll_seconds','max_hold_sec',"
                   "'moonbag_inactivity_sec','moonbag_max_hold_sec')"):
    print(" ", dict(r))

print("position cols:", [r[1] for r in c.execute("PRAGMA table_info(positions)")])

print("open positions:")
for r in c.execute("SELECT * FROM positions WHERE status='open'"):
    print(" ", dict(r))

print("stats rows by status:")
for r in c.execute("SELECT status, COUNT(*) n FROM positions GROUP BY status"):
    print(" ", dict(r))
