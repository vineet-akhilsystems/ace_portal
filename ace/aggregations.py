"""Pure, in-memory helpers over already-fetched rows (no DB, easy to test)."""
import difflib
from collections import Counter
from datetime import date, datetime

# --- categorical columns you can break down / group by ---------------------
COUNTABLE = {
    "Task Name", "ASPL Status", "Client Status", "Priority Name",
    "Module Name", "Client Name", "Employee Name", "Reported By",
    "Lead Name", "ModuleProject",
}

# Friendly filter name -> actual column. Used by search + count tools so the
# same vocabulary works everywhere.
FILTERABLE = {
    "client_name": "Client Name",
    "employee_name": "Employee Name",
    "task_type": "Task Name",
    "priority": "Priority Name",
    "aspl_status": "ASPL Status",
    "client_status": "Client Status",
    "module": "Module Name",
    "reported_by": "Reported By",
    "completed_by": "CompletedBy",
    "assigned_by": "Task AssignBy",
}

# Friendly date-field name -> actual (text) column.
DATE_FIELDS = {
    "raised": "Date",
    "completed": "CompletedOn",
    "target": "Target Date",
    "assigned": "Assigned On",
}

# ASPL statuses that mean the work is finished/closed. Anything NOT in this set
# (Open, Working, Pending, Not Assigned, Under Discussion, Halt) counts as
# still-active/open. Documented so the "open" definition is transparent.
DONE_ASPL_STATUSES = {
    "Delivered", "Verified", "Completed", "Delivered On UAT",
    "Cancel", "Not Feasible",
}

# Ordering for priority-based sorts / severity.
PRIORITY_ORDER = {"Immediate": 0, "High": 1, "Medium": 2, "Low": 3}


def contains(haystack, needle: str) -> bool:
    """Case-insensitive substring match, None-safe."""
    if haystack is None:
        return False
    return needle.lower() in str(haystack).lower()


def breakdown(rows: list[dict], col: str) -> list[dict]:
    """Count non-empty values of `col` across rows, largest first."""
    counter = Counter()
    for r in rows:
        v = r.get(col)
        if v is not None and str(v).strip() != "":
            counter[str(v).strip()] += 1
    return [{"value": k, "count": n} for k, n in counter.most_common()]


def distinct_employee_names(rows: list[dict]) -> list[str]:
    """Every distinct non-empty Employee Name in `rows`, sorted."""
    return sorted({str(r.get("Employee Name") or "").strip()
                   for r in rows if str(r.get("Employee Name") or "").strip()})


def _normalize_ws(s) -> str:
    """Collapse runs of whitespace to a single space, lowercased. The source
    data has inconsistent double-spacing between first/last names (e.g.
    "Siddharth  Sharma") which would otherwise break substring matching on a
    normally-spaced query."""
    return " ".join(str(s).split()).lower()


def resolve_employee(rows: list[dict], query: str):
    """Resolve a user-typed name against the distinct Employee Name values.

    Case-insensitive substring match first (e.g. "siddharth" -> "Siddharth
    Sharma"). A query like "Kumar" can legitimately match several different
    people here (Kuldeep Kumar, Namit Kumar, Amit Kumar Gupta, ...) — that's
    real ambiguity in the data, not a bug, so callers must handle >1 match.

    If nothing contains the query (likely a typo), falls back to fuzzy
    matching against all distinct names so a close spelling still surfaces.
    Fuzzy matching is ONLY used as that fallback — never mixed with substring
    results — because names differing by one letter (e.g. "Siddharth Sharma"
    vs "Siddharta Tiwari") are different people, not typos of each other.

    Returns (exact_matches, fuzzy_suggestions): `exact_matches` is the list
    of distinct names containing `query`; `fuzzy_suggestions` is populated
    only when `exact_matches` is empty.
    """
    names = distinct_employee_names(rows)
    q = _normalize_ws(query)
    if not q:
        return [], []
    matches = [n for n in names if q in _normalize_ws(n)]
    if matches:
        return matches, []
    suggestions = difflib.get_close_matches(
        _normalize_ws(query), [_normalize_ws(n) for n in names], n=5, cutoff=0.6)
    # map normalized suggestions back to their original (as-stored) names
    by_norm = {_normalize_ws(n): n for n in names}
    suggestions = [by_norm[s] for s in suggestions]
    return [], suggestions


def is_open(row: dict) -> bool:
    """True if the ticket is still active (ASPL Status not a 'done' status)."""
    status = str(row.get("ASPL Status") or "").strip()
    return status not in DONE_ASPL_STATUSES


def is_unassigned(row: dict) -> bool:
    """True if nobody is on it yet."""
    return str(row.get("ASPL Status") or "").strip() == "Not Assigned"


def ageing_of(row: dict):
    """Return the ageing-in-days int, or None if absent."""
    v = row.get("Agening")
    return v if isinstance(v, int) else None


def parse_date(text):
    """Parse the view's text dates (dd/MM/yyyy, optional time) -> date|None."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(text):
    """Parse the view's text timestamps (dd/MM/yyyy, optional H:M[:S]) into a
    full datetime (unlike parse_date, keeps the time-of-day) -> datetime|None.
    Needed for duration math (e.g. CompletedOn - WorkedOn)."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_iso(text):
    """Parse an ISO 'YYYY-MM-DD' (or dd/MM/yyyy) filter value -> date|None."""
    if not text:
        return None
    s = str(text).strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        return parse_date(s)


def apply_filters(rows: list[dict], filters: dict) -> list[dict]:
    """Apply the FILTERABLE text filters plus optional `text` (Description).
    Each is a case-insensitive 'contains'; empty/None values are ignored."""
    out = rows
    for key, col in FILTERABLE.items():
        val = filters.get(key)
        if val:
            out = [r for r in out if contains(r.get(col), val)]
    if filters.get("text"):
        out = [r for r in out if contains(r.get("Description"), filters["text"])]
    return out
