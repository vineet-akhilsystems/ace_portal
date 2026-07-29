"""
Database access with a single in-memory cached snapshot.

The view is expensive to run and sits behind a flaky public-IP link, so we
fetch the whole thing once and cache it. Every tool answers from the snapshot.
"""
from time import monotonic, sleep

import pyodbc

from .config import CACHE_TTL, CONN_STR, QUERY_TIMEOUT, VIEW_NAME
from .serialization import json_safe

# One cached snapshot of the whole view, shared across all tool calls.
_CACHE = {"rows": None, "fetched_at": 0.0}

# SQLSTATE / error fragments worth retrying with a fresh connection.
_TRANSIENT = ("08S01", "08001", "HYT00", "HYT01", "10053", "10054", "timeout")


def _fetch_all_from_db() -> list[dict]:
    """Run the view once and return every row as a dict.
    Retries on transient link failures with a fresh connection."""
    sql = f"SELECT * FROM {VIEW_NAME} ORDER BY sno DESC;"
    last_err = None
    for attempt in range(3):
        conn = None
        try:
            conn = pyodbc.connect(CONN_STR, timeout=15)
            conn.timeout = QUERY_TIMEOUT  # query (not just login) timeout
            cur = conn.cursor()
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [
                {col: json_safe(val) for col, val in zip(cols, row)}
                for row in cur.fetchall()
            ]
        except pyodbc.Error as e:
            last_err = e
            if not any(t in str(e) for t in _TRANSIENT):
                raise  # a real error (auth, bad SQL) — don't retry
            sleep(1.5 * (attempt + 1))  # brief backoff, then reconnect
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError(
        f"Could not fetch the view after 3 attempts (flaky link). "
        f"Last error: {last_err}")


def get_rows(force: bool = False) -> list[dict]:
    """Return the cached snapshot, refreshing it if stale or forced."""
    fresh = (_CACHE["rows"] is not None
             and (monotonic() - _CACHE["fetched_at"]) < CACHE_TTL)
    if force or not fresh:
        _CACHE["rows"] = _fetch_all_from_db()
        _CACHE["fetched_at"] = monotonic()
    return _CACHE["rows"]
