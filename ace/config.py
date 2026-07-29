"""Configuration — loads settings from the project-root .env once.

All other modules import their settings from here, so there is a single place
that knows about environment variables and the connection string.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = one level above this package directory. The .env lives there.
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "").strip()
DB_PORT = os.getenv("DB_PORT", "1433").strip()
DB_NAME = os.getenv("DB_NAME", "").strip()
DB_USER = os.getenv("DB_USER", "").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "").strip()
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server").strip()

# The reporting view this server is built around.
VIEW_NAME = "vwemployeerptclientforsanjaysir"

# How long (seconds) a cached snapshot stays valid before a re-fetch.
CACHE_TTL = int(os.getenv("DB_CACHE_TTL", "120"))
# Per-query timeout (seconds) so a stalled link fails fast instead of hanging.
QUERY_TIMEOUT = int(os.getenv("DB_QUERY_TIMEOUT", "60"))

CONN_STR = (
    f"DRIVER={{{DB_DRIVER}}};"
    f"SERVER={DB_HOST},{DB_PORT};"
    + (f"DATABASE={DB_NAME};" if DB_NAME else "")
    + f"UID={DB_USER};"
    f"PWD={DB_PASSWORD};"
    "TrustServerCertificate=yes;"
    "Encrypt=yes;"
)
