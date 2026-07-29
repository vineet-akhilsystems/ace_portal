"""Find which database on the server contains the target view.

Run from anywhere:  py scripts/find_view.py
"""
import os
from pathlib import Path

import pyodbc
from dotenv import load_dotenv

# .env lives at the project root (one level above this scripts/ folder).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
HOST = os.getenv("DB_HOST").strip()
PORT = os.getenv("DB_PORT", "1433").strip()
USER = os.getenv("DB_USER").strip()
PWD = os.getenv("DB_PASSWORD").strip()
DRIVER = os.getenv("DB_DRIVER").strip()
TARGET = "vwemployeerptclientforsanjaysir"

base = (f"DRIVER={{{DRIVER}}};SERVER={HOST},{PORT};UID={USER};PWD={PWD};"
        "TrustServerCertificate=yes;Encrypt=yes;")

conn = pyodbc.connect(base, timeout=8)
cur = conn.cursor()
cur.execute("SELECT name FROM sys.databases WHERE state = 0 ORDER BY name;")
dbs = [r[0] for r in cur.fetchall()]
conn.close()
print(f"Accessible databases ({len(dbs)}): {dbs}\n")

found = []
for db in dbs:
    try:
        c = pyodbc.connect(base + f"DATABASE={db};", timeout=8)
        cc = c.cursor()
        cc.execute(
            "SELECT s.name, o.type_desc FROM sys.objects o "
            "JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "WHERE o.name = ?;", TARGET)
        for schema, typ in cc.fetchall():
            found.append((db, schema, typ))
            print(f"  FOUND in [{db}].[{schema}]  ({typ})")
        c.close()
    except pyodbc.Error as e:
        print(f"  (skip {db}: {e.args[0]})")

if found:
    db, schema, _ = found[0]
    print(f"\n>> Use DB_NAME={db}")
    print(f">> Fully-qualified: [{db}].[{schema}].[{TARGET}]")
else:
    print(f"\n>> '{TARGET}' not found in any accessible database.")
