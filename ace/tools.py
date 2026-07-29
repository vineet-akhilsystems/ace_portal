"""The MCP tools. Each answers from the cached view snapshot (see database.py).

Three layers:
  * primitives  — search_tickets, count_by, get_ticket (compose anything)
  * dashboards  — workload, client_summary, overdue_or_aged, search_description
  * insights    — executive_summary, team_performance, clients_needing_attention,
                  priority_watch  (CEO/CTO/PM rollups)

Importing this module registers every tool on the shared `mcp` instance.
"""
from collections import Counter
from datetime import datetime, timedelta

from . import insights
from .aggregations import (
    COUNTABLE,
    DATE_FIELDS,
    ageing_of,
    apply_filters,
    breakdown,
    contains,
    is_open,
    parse_date,
    parse_datetime,
    parse_iso,
    resolve_employee,
)
from .config import CACHE_TTL
from .database import get_rows
from .insights import sort_rows
from .server import mcp


def _today():
    return datetime.now().date()


# --- basics ---------------------------------------------------------------
@mcp.tool()                                                                                 
def get_employee_client_report(limit: int = 100) -> list[dict]:
    """Return rows from the vwemployeerptclientforsanjaysir reporting view,
    newest first (ordered by sno descending).

    Args:
        limit: Max rows to return (default 100, max 5000). Pass 0 for all.
    """
    limit = max(0, min(int(limit), 5000))
    rows = get_rows()
    return rows if limit == 0 else rows[:limit]


@mcp.tool()
def get_report_row_count() -> int:
    """Return the total number of rows in the reporting view."""
    return len(get_rows())


@mcp.tool()
def refresh_data() -> dict:
    """Force a fresh pull of the view from the database, bypassing the cache.
    Use when you need the very latest data (e.g. after new tasks were added)."""
    rows = get_rows(force=True)
    return {"refreshed": True, "row_count": len(rows),
            "cache_ttl_seconds": CACHE_TTL}


# --- primitives -----------------------------------------------------------
@mcp.tool()
def search_tickets(
    client_name: str | None = None,
    employee_name: str | None = None,
    task_type: str | None = None,
    priority: str | None = None,
    aspl_status: str | None = None,
    client_status: str | None = None,
    module: str | None = None,
    reported_by: str | None = None,
    completed_by: str | None = None,
    assigned_by: str | None = None,
    text: str | None = None,
    only_open: bool = False,
    overdue: bool = False,
    date_field: str | None = None,
    last_days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    sort_by: str = "newest",
    limit: int = 100,
) -> dict:
    """The workhorse lookup. Filter tickets by ANY combination of fields, dates
    and status, then sort. All text filters are case-insensitive 'contains' and
    combine with AND; leave a filter as None to ignore it.

    Args:
        client_name / employee_name / reported_by / completed_by / assigned_by:
            people & client filters.
        task_type:    Bug, New Requirement, Change/Enhancement, Activity, ...
        priority:     Immediate, High, Medium, Low.
        aspl_status:  Open, Delivered, Verified, Working, Not Assigned, ...
        client_status: Open, Done, Cancel.
        module:       Module Name (e.g. Augastam EMR).
        text:         substring to find in the Description.
        only_open:    keep only still-active tickets (ASPL Status not a done state).
        overdue:      keep only open tickets whose Target Date is in the past.
        date_field:   one of raised, completed, target, assigned — which date the
                      last_days/since/until window applies to.
        last_days:    with date_field, keep rows in the last N days.
        since / until: with date_field, ISO 'YYYY-MM-DD' bounds (inclusive).
        sort_by:      newest (default), oldest, ageing, priority.
        limit:        max rows returned (default 100, max 5000).

    Returns a dict: matched (total), returned, unparsed_dates (rows skipped
    because a text date couldn't be parsed), and rows.
    """
    filters = {
        "client_name": client_name, "employee_name": employee_name,
        "task_type": task_type, "priority": priority,
        "aspl_status": aspl_status, "client_status": client_status,
        "module": module, "reported_by": reported_by,
        "completed_by": completed_by, "assigned_by": assigned_by, "text": text,
    }
    rows = apply_filters(get_rows(), filters)
    today = _today()
    unparsed = 0

    if only_open:
        rows = [r for r in rows if is_open(r)]

    if overdue:
        kept = []
        for r in rows:
            if not is_open(r):
                continue
            d = parse_date(r.get("Target Date"))
            if d is None:
                unparsed += 1
                continue
            if d < today:
                kept.append(r)
        rows = kept

    if date_field:
        col = DATE_FIELDS.get(date_field)
        if col is None:
            raise ValueError(
                f"date_field must be one of {sorted(DATE_FIELDS)}")
        lo = (today - timedelta(days=int(last_days))) if last_days else parse_iso(since)
        hi = parse_iso(until)
        kept = []
        for r in rows:
            d = parse_date(r.get(col))
            if d is None:
                unparsed += 1
                continue
            if lo and d < lo:
                continue
            if hi and d > hi:
                continue
            kept.append(r)
        rows = kept

    rows = sort_rows(rows, sort_by)
    limit = max(1, min(int(limit), 5000))
    return {
        "matched": len(rows),
        "returned": min(len(rows), limit),
        "unparsed_dates": unparsed,
        "rows": rows[:limit],
    }


@mcp.tool()
def get_ticket(aspl_number: str) -> dict:
    """Look up a single ticket by its ASPL# (e.g. 'ASPL-165661' or just
    '165661'). Returns full details including assignee, priority, status and
    dates."""
    q = str(aspl_number).strip().lower()
    rows = get_rows()
    exact = [r for r in rows
             if str(r.get("ASPL#") or "").strip().lower() in (q, f"aspl-{q}")]
    matches = exact or [r for r in rows
                        if q and q in str(r.get("ASPL#") or "").strip().lower()]
    return {"query": aspl_number, "found": bool(matches),
            "count": len(matches), "tickets": matches}


@mcp.tool()
def count_by(
    column: str,
    second_dimension: str | None = None,
    only_open: bool = False,
    avg_ageing: bool = False,
) -> list[dict]:
    """Group the view by a categorical column and count, largest first.

    Args:
        column:           the column to group by (see allowed list below).
        second_dimension: optional second column for a cross-tab (e.g.
                          count_by('Priority Name', 'ASPL Status')).
        only_open:        count only still-active tickets.
        avg_ageing:       also report average ageing (days) per group.

    Allowed columns: Task Name, ASPL Status, Client Status, Priority Name,
    Module Name, Client Name, Employee Name, Reported By, Lead Name,
    ModuleProject.
    """
    if column not in COUNTABLE:
        raise ValueError(
            f"Column '{column}' not allowed. Choose one of: {sorted(COUNTABLE)}")
    rows = get_rows()
    if only_open:
        rows = [r for r in rows if is_open(r)]

    if second_dimension:
        if second_dimension not in COUNTABLE:
            raise ValueError(
                f"second_dimension '{second_dimension}' not allowed. "
                f"Choose one of: {sorted(COUNTABLE)}")
        grouped: dict[str, Counter] = {}
        for r in rows:
            k1 = str(r.get(column) or "").strip()
            k2 = str(r.get(second_dimension) or "").strip()
            if k1 and k2:
                grouped.setdefault(k1, Counter())[k2] += 1
        return [
            {
                "value": k1,
                "count": sum(c.values()),
                "breakdown": [{"value": k2, "count": n}
                              for k2, n in c.most_common()],
            }
            for k1, c in sorted(grouped.items(),
                                key=lambda kv: sum(kv[1].values()), reverse=True)
        ]

    result = breakdown(rows, column)
    if avg_ageing:
        sums: dict[str, int] = {}
        counts: dict[str, int] = {}
        for r in rows:
            k = str(r.get(column) or "").strip()
            a = ageing_of(r)
            if k and a is not None:
                sums[k] = sums.get(k, 0) + a
                counts[k] = counts.get(k, 0) + 1
        for item in result:
            k = item["value"]
            item["avg_ageing_days"] = (
                round(sums[k] / counts[k], 1) if counts.get(k) else None)
    return result


# --- single-entity dashboards ---------------------------------------------
@mcp.tool()
def overdue_or_aged(min_days: int = 30, limit: int = 100) -> list[dict]:
    """Return tasks whose ageing (`Agening`, in days) is at least `min_days`,
    oldest first. Use for stale / SLA-breach questions.

    Args:
        min_days: minimum ageing in days (default 30).
        limit:    max rows (default 100, max 5000).
    """
    min_days = max(0, int(min_days))
    limit = max(1, min(int(limit), 5000))
    aged = [r for r in get_rows()
            if isinstance(r.get("Agening"), int) and r["Agening"] >= min_days]
    aged.sort(key=lambda r: (r["Agening"], r.get("sno", 0)), reverse=True)
    return aged[:limit]


@mcp.tool()
def find_employee(query: str, limit: int = 10) -> dict:
    """Search the Employee Name column — use this first when you're not sure
    of the exact spelling, or before workload/employee_effort if the name
    might be ambiguous (e.g. 'Kumar' matches several different people).

    Tries a case-insensitive substring match first. If nothing contains the
    query (likely a typo), falls back to fuzzy matching against every
    distinct name so a close-but-imperfect spelling still surfaces
    candidates.

    Args:
        query: full or partial employee name.
        limit: max candidates to return (default 10).

    Returns exact (bool — whether substring matches were found, as opposed
    to fuzzy fallback) and candidates: [{name, total, open}], largest first.
    """
    rows = get_rows()
    matches, suggestions = resolve_employee(rows, query)
    names = matches or suggestions
    candidates = []
    for name in names:
        ers = [r for r in rows
               if str(r.get("Employee Name") or "").strip() == name]
        candidates.append({
            "name": name,
            "total": len(ers),
            "open": sum(1 for r in ers if is_open(r)),
        })
    candidates.sort(key=lambda c: c["total"], reverse=True)
    limit = max(1, int(limit))
    return {
        "query": query,
        "exact": bool(matches),
        "candidates": candidates[:limit],
    }


def _resolve_or_ambiguity(all_rows: list[dict], query: str):
    """Shared employee-name resolution for the per-employee dashboards.

    Returns (exact_name, rows) on a single match. On zero or multiple
    matches, returns (None, payload) where `payload` is the dict the calling
    tool should return as-is (not-found w/ fuzzy suggestions, or ambiguous
    w/ candidate list) instead of guessing or silently merging people.
    """
    matches, suggestions = resolve_employee(all_rows, query)

    if not matches:
        return None, {"employee": query, "found": False,
                       "did_you_mean": suggestions}

    if len(matches) > 1:
        by_name = {n: [r for r in all_rows
                        if str(r.get("Employee Name") or "").strip() == n]
                   for n in matches}
        return None, {
            "employee": query,
            "ambiguous": True,
            "hint": "Multiple employees match — call again with the exact "
                    "'name' from matches.",
            "matches": sorted(
                ({"name": n, "total": len(rs),
                  "open": sum(1 for r in rs if is_open(r))}
                 for n, rs in by_name.items()),
                key=lambda m: m["total"], reverse=True),
        }

    exact_name = matches[0]
    rows = [r for r in all_rows
            if str(r.get("Employee Name") or "").strip() == exact_name]
    return exact_name, rows


@mcp.tool()
def workload(employee_name: str) -> dict:
    """Deep-dive on one employee: total tasks plus breakdowns by ASPL Status
    and Priority. (For comparing everyone, use team_performance.)

    `employee_name` is matched case-insensitively against distinct Employee
    Name values. If it matches more than one person (e.g. 'Kumar'), returns
    the list of candidates instead of silently merging their tickets — call
    again with the exact name from `matches`. If nothing matches, returns
    `did_you_mean` fuzzy suggestions (use find_employee to explore further).
    """
    all_rows = get_rows()
    exact_name, resolved = _resolve_or_ambiguity(all_rows, employee_name)
    if exact_name is None:
        return resolved
    rows = resolved
    return {
        "employee": exact_name,
        "matched_query": employee_name,
        "total": len(rows),
        "open": sum(1 for r in rows if is_open(r)),
        "by_aspl_status": breakdown(rows, "ASPL Status"),
        "by_priority": breakdown(rows, "Priority Name"),
    }


@mcp.tool()
def employee_effort(
    employee_name: str,
    since: str | None = None,
    until: str | None = None,
    hours_per_day: float = 9,
) -> dict:
    """How much work an employee has actually put in, measured from real
    ticket timestamps: hours = CompletedOn - WorkedOn, summed per ticket.

    WorkedOn is stamped when the employee actually starts a ticket (not when
    it was assigned). Only tickets with BOTH WorkedOn and CompletedOn count
    toward `measured` — that's raw elapsed wall-clock time between the two,
    not capped to a working day, so a ticket left open over a weekend will
    show inflated hours. Tickets whose ASPL Status is still active (WorkedOn
    set, no CompletedOn yet) are reported separately under `in_progress`,
    using "now" as the still-ticking end point. Tickets whose ASPL Status is
    already a done state but never got a CompletedOn stamp (a real data gap)
    land in `unmeasurable` instead of being guessed at as "in progress".

    WorkedOn is a new, sparsely-populated column (~14% of tickets overall as
    of 2026-07-28) — see `coverage` in the result. This measures only what's
    directly evidenced; it will under-count real effort on tickets where
    WorkedOn was never recorded.

    Args:
        employee_name: full or partial employee name (see workload() for the
                        ambiguity/typo handling).
        since / until: optional ISO 'YYYY-MM-DD' bounds — keep only tickets
                        whose WorkedOn date falls in this window (inclusive).
                        Omit for all tickets ever.
        hours_per_day: used only to express `measured.total_hours` as a
                       working-days-equivalent (default 9); not used in the
                       hours calculation itself.
    """
    all_rows = get_rows()
    exact_name, resolved = _resolve_or_ambiguity(all_rows, employee_name)
    if exact_name is None:
        return resolved
    rows = resolved

    lo = parse_iso(since) if since else None
    hi = parse_iso(until) if until else None
    now = datetime.now()

    with_workedon = 0
    finished_count, finished_hours = 0, 0.0
    in_progress_count, in_progress_hours = 0, 0.0
    unmeasurable_count = 0

    for r in rows:
        wo = parse_datetime(r.get("WorkedOn"))
        if wo is None:
            continue
        with_workedon += 1
        if lo and wo.date() < lo:
            continue
        if hi and wo.date() > hi:
            continue

        co = parse_datetime(r.get("CompletedOn"))
        if co is not None:
            delta = (co - wo).total_seconds() / 3600
            if delta >= 0:
                finished_count += 1
                finished_hours += delta
        elif is_open(r):
            # Genuinely still active (e.g. ASPL Status = Working) — clock is
            # still running, measure elapsed time up to now.
            delta = (now - wo).total_seconds() / 3600
            if delta >= 0:
                in_progress_count += 1
                in_progress_hours += delta
        else:
            # ASPL Status says it's done (e.g. Delivered) but CompletedOn was
            # never stamped — can't compute a duration; don't guess.
            unmeasurable_count += 1

    total = len(rows)
    return {
        "employee": exact_name,
        "matched_query": employee_name,
        "found": True,
        "period": ({"since": since, "until": until} if (since or until)
                    else "all-time"),
        "measured": {
            "tickets": finished_count,
            "total_hours": round(finished_hours, 1),
            "working_days_equivalent": (
                round(finished_hours / hours_per_day, 1)
                if hours_per_day else None),
        },
        "in_progress": {
            "tickets": in_progress_count,
            "elapsed_hours_so_far": round(in_progress_hours, 1),
        },
        "unmeasurable": {
            "tickets": unmeasurable_count,
            "reason": ("ASPL Status is a done state but CompletedOn is "
                        "missing, so no end timestamp to measure against."),
        },
        "coverage": {
            "total_tickets": total,
            "tickets_with_workedon": with_workedon,
            "pct_with_workedon": (
                round(100 * with_workedon / total, 1) if total else 0),
        },
    }


@mcp.tool()
def client_summary(client_name: str) -> dict:
    """Deep-dive on one client: total tasks plus breakdowns by Task type, ASPL
    Status and Priority. (For ranking all clients by risk, use
    clients_needing_attention.)"""
    rows = [r for r in get_rows()
            if contains(r.get("Client Name"), client_name)]
    return {
        "client": client_name,
        "total": len(rows),
        "open": sum(1 for r in rows if is_open(r)),
        "by_task_type": breakdown(rows, "Task Name"),
        "by_aspl_status": breakdown(rows, "ASPL Status"),
        "by_priority": breakdown(rows, "Priority Name"),
    }


@mcp.tool()
def search_description(text: str, limit: int = 50) -> list[dict]:
    """'Contains' search over the Description column, newest first. (Same as
    search_tickets(text=...) but returns just the row list.)"""
    limit = max(1, min(int(limit), 5000))
    out = [r for r in get_rows() if contains(r.get("Description"), text)]
    return out[:limit]


# --- executive / management insights --------------------------------------
@mcp.tool()
def executive_summary(aged_days: int = 30) -> dict:
    """Portfolio health snapshot for leadership: totals, open vs closed,
    unassigned, immediate/high open, overdue & aged counts, bug share, and the
    top clients/employees by open load. One call = the whole picture.

    Args:
        aged_days: the ageing threshold (days) used for the aged-open bucket.
    """
    return insights.executive_summary(get_rows(), _today(), int(aged_days))


@mcp.tool()
def team_performance(aged_days: int = 30) -> dict:
    """Per-employee accountability & workload balance, ranked by open load:
    total, open, immediate/high open, aged & overdue, oldest open item, and
    completion rate. Answers "who's doing what, who's overloaded, who's behind".
    """
    return insights.team_performance(get_rows(), _today(), int(aged_days))


@mcp.tool()
def clients_needing_attention(aged_days: int = 30, top: int = 10) -> dict:
    """Clients ranked by escalation risk (weighted from immediate-open, aged &
    overdue, and raw open work). Answers "which clients need attention now".

    Args:
        aged_days: ageing threshold (days) for the aged-open signal.
        top:       how many clients to return (default 10).
    """
    top = max(1, int(top))
    return {
        "aged_days": int(aged_days),
        "clients": insights.clients_needing_attention(
            get_rows(), _today(), int(aged_days), top),
    }


@mcp.tool()
def priority_watch(aged_days: int = 30) -> dict:
    """Are the important things being handled? For Immediate and High priority:
    how much is open, unassigned and aged, plus the worst-offending open tickets.
    """
    return insights.priority_watch(get_rows(), _today(), int(aged_days))
