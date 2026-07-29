"""Quick connectivity check for the ACE MCP database.

Runs three checks:
  1. TCP reachability to DB_HOST:DB_PORT  (is the server reachable at all?)
  2. Authentication + connect to the database
  3. The actual view query (limited to a few rows)

Run from anywhere:  py scripts/test_connection.py
"""
import os
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

# .env lives at the project root (one level above this scripts/ folder).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HOST = os.getenv("DB_HOST", "").strip()
PORT = int(os.getenv("DB_PORT", "1433").strip() or "1433")
NAME = os.getenv("DB_NAME", "").strip()
USER = os.getenv("DB_USER", "").strip()
PWD = os.getenv("DB_PASSWORD", "").strip()
DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server").strip()

VIEW_QUERY = "SELECT TOP 5 * FROM vwemployeerptclientforsanjaysir ORDER BY sno DESC;"


def fail(msg):
    print(f"\n[FAIL] {msg}")
    sys.exit(1)


# --- 0. sanity: are the values present? (DB_NAME is optional) ---
missing = [k for k, v in {
    "DB_HOST": HOST, "DB_USER": USER, "DB_PASSWORD": PWD
}.items() if not v]
if missing:
    fail(f"These .env values are still empty: {', '.join(missing)}")
if not NAME:
    print("[note] DB_NAME is empty -> will use the login's DEFAULT database.")

# --- 1. TCP reachability ---
print(f"[1/3] Testing TCP connection to {HOST}:{PORT} ...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(6)
try:
    sock.connect((HOST, PORT))
    print("      OK - port is open and reachable.")
except Exception as e:
    fail(f"Cannot reach {HOST}:{PORT} -> {e}\n"
         "      Likely: wrong IP/port, firewall, VPN needed, or SQL Server "
         "TCP/IP not enabled.")
finally:
    sock.close()

# --- 2. DB connect + auth ---
import pyodbc

conn_str = (
    f"DRIVER={{{DRIVER}}};"
    f"SERVER={HOST},{PORT};"
    + (f"DATABASE={NAME};" if NAME else "")
    + f"UID={USER};"
    f"PWD={PWD};"
    "TrustServerCertificate=yes;"
    "Encrypt=yes;"
)
print(f"[2/3] Connecting to database '{NAME}' as '{USER}' ...")
try:
    conn = pyodbc.connect(conn_str, timeout=8)
    print("      OK - authenticated and connected.")
except pyodbc.Error as e:
    fail(f"Connection/auth failed -> {e}")

# --- 3. Run the view query ---
print("[3/3] Running the view query (TOP 5) ...")
try:
    cur = conn.cursor()
    cur.execute(VIEW_QUERY)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    print(f"      OK - query ran. Columns ({len(cols)}): {cols}")
    print(f"      Sample rows returned: {len(rows)}")
    for r in rows:
        print("      ", tuple(r))
except pyodbc.Error as e:
    fail(f"Query failed -> {e}\n"
         "      (Reached the DB, but the view/permission may be the issue.)")
finally:
    conn.close()

print("\n[SUCCESS] You can access the data. Ready to build the MCP.")
