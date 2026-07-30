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


def _token_sim(qtok: str, ntok: str) -> float:
    """Similarity 0..1 between ONE query token and ONE name token."""
    if qtok == ntok:
        return 1.0
    # prefix / initial: 'sid' -> 'siddharth', 's' -> 'sharma'
    if ntok.startswith(qtok) or qtok.startswith(ntok):
        return 0.95
    if qtok in ntok or ntok in qtok:
        return 0.9
    return difflib.SequenceMatcher(None, qtok, ntok).ratio()


def _query_name_score(qtokens: list[str], ntokens: list[str]) -> float:
    """Token-aware match score for a whole query against a whole name.

    Each query token must find a *decent* name token (best >= 0.5) or the
    match is rejected outright; otherwise the score is the average of the
    per-token best matches. This lets a misspelled first name still match
    ('sidharth' -> 'Siddharth Sharma') and a first-name-or-last-name-only query
    score high, while a single strong token can't drag in an unrelated person
    ('sharma qwerty' is rejected because 'qwerty' matches nothing)."""
    if not qtokens or not ntokens:
        return 0.0
    bests = [max((_token_sim(qt, nt) for nt in ntokens), default=0.0)
             for qt in qtokens]
    if min(bests) < 0.5:
        return 0.0
    return sum(bests) / len(bests)


def resolve_employee(rows: list[dict], query: str, fuzzy_cutoff: float = 0.72):
    """Resolve a user-typed name against the distinct Employee Name values,
    tolerant of typos, first/last-name-only queries, initials and variations.

    Two tiers:

    * **Tier 1 — substring** on the full normalized name (e.g. "siddharth",
      "sharma", "sid" all hit "Siddharth Sharma"). Treated as real matches. A
      query like "Kumar" can legitimately match several *different* people
      (Kuldeep Kumar, Namit Kumar, ...) — real ambiguity, so callers must
      handle >1 match and must NOT silently merge them.

    * **Tier 2 — token-aware fuzzy** fallback, used ONLY when Tier 1 finds
      nothing (likely a misspelling). Scores each distinct name with
      `_query_name_score` so "Sidharth", "Kuldeep Kumr", "S Sharma" still
      surface the right person. Returned as *suggestions*, never as matches —
      names one letter apart can be different people, so a human/LLM confirms.

    Returns (matches, suggestions): `matches` is the Tier-1 substring list;
    `suggestions` (best-first) is populated only when `matches` is empty.
    """
    names = distinct_employee_names(rows)
    qn = _normalize_ws(query)
    if not qn:
        return [], []

    substr = [n for n in names if qn in _normalize_ws(n)]
    if substr:
        return substr, []

    return [], [n for n, _ in scored_fuzzy_candidates(rows, query, fuzzy_cutoff)[:5]]


def scored_fuzzy_candidates(rows: list[dict], query: str,
                            cutoff: float = 0.72) -> list[tuple[str, float]]:
    """The Tier-2 fuzzy candidates as (name, score) pairs, best-first. Only
    meaningful when there is no substring match. Shared by `resolve_employee`
    (which drops the scores) and `confident_fuzzy_match` (which needs them)."""
    names = distinct_employee_names(rows)
    qn = _normalize_ws(query)
    if not qn:
        return []
    qtokens = qn.split()
    scored = [(n, _query_name_score(qtokens, _normalize_ws(n).split()))
              for n in names]
    scored = [(n, s) for n, s in scored if s >= cutoff]
    scored.sort(key=lambda ns: ns[1], reverse=True)
    return scored


def confident_fuzzy_match(rows: list[dict], query: str,
                          min_score: float = 0.85, margin: float = 0.10):
    """Return the ONE unambiguous best fuzzy name for a misspelling, or None.

    Auto-resolving a typo is only safe when there is a clear single winner:
    the top candidate must clear `min_score` AND stand clear of the runner-up
    by `margin`. So "swadin" -> "Swadhin Kumar Senapati" resolves (no rival),
    and "sidharth" resolves to "Siddharth Sharma" (a clear leader over the more
    distant "Siddharta Tiwari"). But a shared-prefix tie like "Siddhart" — an
    exact prefix of BOTH those names — does NOT resolve; it stays a suggestion
    for a human/LLM to confirm. Whether a substring match existed first is the
    caller's job to check; this only inspects the fuzzy pool."""
    cands = scored_fuzzy_candidates(rows, query)
    if not cands:
        return None
    top_name, top_score = cands[0]
    if top_score < min_score:
        return None
    if len(cands) > 1 and (top_score - cands[1][1]) < margin:
        return None
    return top_name


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
