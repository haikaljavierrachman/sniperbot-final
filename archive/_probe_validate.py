import os
os.environ.pop("TG_API_ID", None)
from sniper import config
try:
    config.validate()
    print("VALIDATE_OK")
except SystemExit as e:
    print("SystemExit:", e)
