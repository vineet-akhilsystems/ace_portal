"""Turn SQL Server values into JSON-serializable Python values."""
from datetime import date, datetime, time
from decimal import Decimal


def json_safe(value):
    """Convert types the JSON encoder can't handle (dates, Decimal, bytes)."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value
