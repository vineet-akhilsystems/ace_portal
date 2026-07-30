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

from . import duplicates, insights, leadership
from .aggregations import (
    COUNTABLE,
    DATE_FIELDS,
    ageing_of,
    apply_filters,
    breakdown,
    confident_fuzzy_match,
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

    A misspelling with ONE clearly-best fuzzy candidate (no close rival) is
    auto-resolved to that person — the caller's result shows `employee` (the
    corrected name) alongside `matched_query` (what was typed), so the fix is
    visible. A typo that sits between two real people is NOT auto-resolved; it
    falls through to `did_you_mean`.
    """
    matches, suggestions = resolve_employee(all_rows, query)

    if not matches:
        guess = confident_fuzzy_match(all_rows, query)
        if guess:
            rows = [r for r in all_rows
                    if str(r.get("Employee Name") or "").strip() == guess]
            return guess, rows
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


# --- deadlines, duplicates, trends, speed ---------------------------------
@mcp.tool()
def target_date_status(
    only_open: bool = True,
    due_soon_days: int = 7,
    group_by: str = "employee",
) -> dict:
    """Where tickets stand against their Target Date — the deadline view.

    Answers questions like "how many employees haven't met their target dates
    yet?" (`employees_with_missed_targets` + the names list) and "what's
    overdue / due soon?".

    Each ticket is bucketed as:
      * missed    — Target Date is already in the past (target NOT yet achieved)
      * due_soon  — Target Date within the next `due_soon_days` days
      * upcoming  — Target Date further out
      * no_target_date — blank/unparseable Target Date

    Args:
        only_open:     consider only still-active tickets (default True). A
                        closed ticket can't "miss" a future deadline, so leave
                        this on for accountability questions.
        due_soon_days: window (days) for the 'due_soon' bucket (default 7).
        group_by:      'employee' (default), 'client', or 'none' — how to break
                        down the missed/due_soon/upcoming counts.

    Returns overall counts, the count + names of employees with missed targets,
    a per-group breakdown (with each group's worst overdue days), and the worst
    overdue tickets overall.
    """
    return insights.target_date_status(
        get_rows(), _today(), only_open=bool(only_open),
        due_soon_days=int(due_soon_days), group_by=group_by)


@mcp.tool()
def find_similar_tickets(
    min_similarity: float = 0.5,
    same_client_only: bool = False,
    min_words: int = 4,
    max_clusters: int = 40,
) -> dict:
    """Find duplicate / near-duplicate tickets — the same issue raised more than
    once as separate ASPL tickets, even when worded differently.

    Compares the word-set of each ticket's Description (Jaccard similarity, so
    re-ordering and minor rewording still match) and groups matching tickets
    into clusters. Use it to de-duplicate a backlog or spot repeatedly-reported
    problems.

    Args:
        min_similarity:  0.0–1.0 threshold (default 0.5). 1.0 = identical
                          wording; ~0.5 catches the same issue reworded. Lower
                          it (e.g. 0.35) to catch looser/heavily-reworded
                          matches, raise it (0.8+) for only near-exact copies.
        same_client_only: if True, only group tickets from the same client
                          (default False — the same bug can hit several clients).
        min_words:        ignore tickets whose Description has fewer than this
                          many distinctive words (default 4) — one-liners can't
                          be judged reliably.
        max_clusters:     max duplicate clusters to return (default 40).

    Returns clusters_found, tickets_in_duplicate_clusters, and the clusters
    (each with size, avg_similarity and the member tickets), biggest first.
    """
    return duplicates.find_similar(
        get_rows(),
        min_similarity=float(min_similarity),
        same_client_only=bool(same_client_only),
        min_words=int(min_words),
        max_clusters=int(max_clusters),
    )


@mcp.tool()
def activity_trend(
    date_field: str = "raised",
    period: str = "month",
    last_n: int = 6,
) -> dict:
    """Ticket volume over time — how many were raised / completed / assigned per
    week or month. Answers "how many did we close this month vs last?".

    Args:
        date_field: which date to bucket on — raised (default), completed,
                     assigned, or target.
        period:     'month' (default) or 'week'.
        last_n:     keep only the most recent N buckets (default 6; 0 = all).

    Returns a chronological series plus how many rows had a blank/unparseable
    date for that field.
    """
    col = DATE_FIELDS.get(date_field)
    if col is None:
        raise ValueError(f"date_field must be one of {sorted(DATE_FIELDS)}")
    if period not in ("month", "week"):
        raise ValueError("period must be 'month' or 'week'")
    return insights.activity_trend(
        get_rows(), col, period=period, last_n=int(last_n))


@mcp.tool()
def resolution_time(group_by: str = "none", top: int = 30) -> dict:
    """How fast tickets get resolved: CompletedOn - Date(raised), in days.

    Only tickets that have BOTH a raised date and a completion date are measured
    (see `coverage_pct` in the result). Optionally ranks groups slowest-average
    first — good for "which employee/client/priority takes longest to close".

    Args:
        group_by: 'none' (default, overall stats only), 'employee', 'client',
                   'priority', or 'task_type'.
        top:       max groups to return when grouping (default 30).

    Returns overall avg/median/min/max days and coverage, plus the per-group
    breakdown when grouped.
    """
    return insights.resolution_time(get_rows(), group_by=group_by, top=int(top))


# --- leadership: support people -------------------------------------------
@mcp.tool()
def stuck_tickets(min_days: int = 14, group_by: str = "employee",
                  limit: int = 100) -> dict:
    """Find open tickets that have gone quiet for a while — usually a sign the
    person is BLOCKED (waiting on a client, a decision, another team), not that
    they forgot. Use it to go unblock people, not to reprimand.

    Args:
        min_days:  how many days idle before a still-open ticket counts as stuck
                    (default 14).
        group_by:  'employee' (default), 'client', or 'none'.
        limit:     max worst tickets to list (default 100).

    Returns total stuck, how many were actively picked up but stalled, a status
    breakdown, the per-group counts, and the worst offenders.
    """
    return leadership.stuck_tickets(
        get_rows(), _today(), min_days=int(min_days), group_by=group_by,
        limit=int(limit))


@mcp.tool()
def workload_balance(aged_days: int = 30) -> dict:
    """Is the workload FAIR? Per-employee open / immediate / high / aged load
    with a spread measure, plus explicit 'overloaded' and 'can absorb more'
    lists — so you can rebalance and relieve people. Also flags weekend/
    after-hours work (from WorkedOn timestamps, where present) as a burnout
    signal. Purpose: support, not rank.

    Args:
        aged_days: ageing threshold (days) for the aged-open count (default 30).
    """
    return leadership.workload_balance(get_rows(), _today(), aged_days=int(aged_days))


@mcp.tool()
def employee_briefing(employee_name: str, aged_days: int = 30) -> dict:
    """A supportive 1:1 prep pack for ONE employee — everything a manager needs
    to have a HELPFUL conversation: current load, what's stuck (where they need
    help), wins to recognise, missed targets, and whether those missed targets
    are actually a systemic problem (their clients' deadlines being missed by
    many people = not their fault), plus their module focus.

    `employee_name` uses the same case-insensitive / ambiguity handling as
    workload() — if it matches several people it returns the candidate list
    instead of merging them; if nothing matches it returns fuzzy suggestions.
    """
    all_rows = get_rows()
    exact_name, resolved = _resolve_or_ambiguity(all_rows, employee_name)
    if exact_name is None:
        return resolved
    out = leadership.employee_briefing(
        resolved, all_rows, _today(), exact_name, aged_days=int(aged_days))
    out["matched_query"] = employee_name  # shows a typo auto-correction
    return out


# --- leadership: fix systemic problems ------------------------------------
@mcp.tool()
def problem_hotspots(group_by: str = "module", top: int = 10) -> dict:
    """Which MODULE or CLIENT keeps generating pain — so you fix the system
    instead of blaming individuals. Ranks by a pain score built from open load,
    bug share, overdue rate, and rework (share of tickets that near-duplicate
    others = wasted effort).

    Args:
        group_by: 'module' (default) or 'client'.
        top:      how many hotspots to return (default 10).
    """
    if group_by not in ("module", "client"):
        raise ValueError("group_by must be 'module' or 'client'")
    return leadership.problem_hotspots(get_rows(), _today(), group_by=group_by,
                                       top=int(top))


@mcp.tool()
def target_realism(group_by: str = "client", min_tickets: int = 5,
                   top: int = 15) -> dict:
    """Are the deadlines even realistic? Finds clients/modules/employees where
    target dates are missed at a high rate ACROSS MANY people — a signal the
    targets are unrealistic (a planning fix), not that individuals are failing.
    Use it to defend the team with data.

    Args:
        group_by:    'client' (default), 'module', or 'employee'.
        min_tickets: ignore groups with fewer than this many open-with-target
                      tickets (default 5) to avoid noise.
        top:         how many groups to return (default 15).
    """
    return leadership.target_realism(get_rows(), _today(), group_by=group_by,
                                     min_tickets=int(min_tickets), top=int(top))


@mcp.tool()
def cycle_time_breakdown(group_by: str = "none", top: int = 15) -> dict:
    """WHERE does work stall? Splits the pipeline into stages and times each:
      triage    = raised -> assigned
      pickup    = assigned -> worked
      execution = worked -> completed
      total     = raised -> completed
    Slow triage or pickup is a process/queue problem, not an employee one. Each
    stage reports its own coverage ('count') because WorkedOn is sparse.

    Args:
        group_by: 'none' (default, overall), 'employee', 'client', or 'module'.
        top:      max groups when grouping (default 15).
    """
    return leadership.cycle_time_breakdown(get_rows(), group_by=group_by,
                                           top=int(top))


# --- leadership: recognise & grow -----------------------------------------
@mcp.tool()
def recognition(since: str | None = None, until: str | None = None,
                top: int = 8) -> dict:
    """The POSITIVE spotlight — who to thank or put forward for growth. Top
    closers, fastest average resolvers, most Immediate fires handled, and best
    target adherence. Most reports only find problems; this finds people doing
    great work.

    Args:
        since / until: optional ISO 'YYYY-MM-DD' window on the COMPLETED date
                        (e.g. 'this month'). Omit for all-time.
        top:           how many people per category (default 8).
    """
    return leadership.recognition(get_rows(), _today(), since=since, until=until,
                                  top=int(top))


@mcp.tool()
def expertise_map(by: str = "module", top: int = 12) -> dict:
    """Who's the go-to SPECIALIST for each module/client (volume + completion),
    and where is the BUS-FACTOR risk — areas where one person holds all the
    knowledge (fragile if they're away, and often a person who's silently
    carrying too much). Use it for smart assignment, spotting mentors, and
    cross-training decisions.

    Args:
        by:  'module' (default) or 'client'.
        top: how many areas to return (default 12).
    """
    if by not in ("module", "client"):
        raise ValueError("by must be 'module' or 'client'")
    return leadership.expertise_map(get_rows(), by=by, top=int(top))


# --- leadership: flow & backlog health ------------------------------------
@mcp.tool()
def backlog_health(period: str = "month", last_n: int = 6) -> dict:
    """Are we keeping up? Compares intake (tickets raised) vs throughput
    (completed) per period, with net change, plus the current open backlog and
    its aged share. net_change > 0 means the backlog grew that period.

    Args:
        period: 'month' (default) or 'week'.
        last_n: most recent N periods to show (default 6; 0 = all).
    """
    if period not in ("month", "week"):
        raise ValueError("period must be 'month' or 'week'")
    return leadership.backlog_health(get_rows(), _today(), period=period,
                                     last_n=int(last_n))


@mcp.tool()
def triage_gaps(slow_days: int = 3, limit: int = 50) -> dict:
    """Where the INTAKE process fails people: unassigned Immediate/High tickets,
    and tickets that sat a long time between raised and assigned. This is a
    front-office/queue problem, not the assignee's fault.

    Args:
        slow_days: raised->assigned lag (days) that counts as slow triage
                    (default 3).
        limit:     max tickets to list per section (default 50).
    """
    return leadership.triage_gaps(get_rows(), _today(), slow_days=int(slow_days),
                                  limit=int(limit))
