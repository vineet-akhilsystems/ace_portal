"""Executive / project-management insight computations (pure functions).

These turn the raw ticket rows into the kind of rollups a CEO/CTO/PM asks for:
portfolio health, team accountability, client escalation risk, and whether the
high-priority work is actually being handled. No DB access here — each takes the
already-fetched rows so it can be unit-tested offline.

"Open" = ASPL Status is not a done status (see aggregations.DONE_ASPL_STATUSES).
"Aged" = ageing (Agening, days) >= aged_days AND still open.
"""
from .aggregations import (
    PRIORITY_ORDER,
    ageing_of,
    breakdown,
    is_open,
    is_unassigned,
    parse_date,
)


def _open(rows):
    return [r for r in rows if is_open(r)]


def _aged_open(rows, aged_days):
    return [r for r in rows
            if is_open(r) and (ageing_of(r) or 0) >= aged_days]


def _overdue(rows, today):
    """Open tickets whose Target Date is in the past."""
    out = []
    for r in rows:
        if not is_open(r):
            continue
        d = parse_date(r.get("Target Date"))
        if d is not None and d < today:
            out.append(r)
    return out


def _oldest_ageing(rows):
    vals = [ageing_of(r) for r in rows if ageing_of(r) is not None]
    return max(vals) if vals else 0


def _top_open_by_group(rows, col, n=5):
    counter = {}
    for r in _open(rows):
        key = str(r.get(col) or "").strip()
        if key:
            counter[key] = counter.get(key, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
    return [{"value": k, "open": v} for k, v in ranked[:n]]


def executive_summary(rows, today, aged_days=30):
    """One-shot portfolio health snapshot for leadership."""
    total = len(rows)
    open_rows = _open(rows)
    immediate_open = [r for r in open_rows
                      if str(r.get("Priority Name") or "").strip() == "Immediate"]
    high_open = [r for r in open_rows
                 if str(r.get("Priority Name") or "").strip() == "High"]
    bugs = [r for r in rows
            if str(r.get("Task Name") or "").strip() == "Bug"]
    return {
        "total_tickets": total,
        "open": len(open_rows),
        "closed": total - len(open_rows),
        "unassigned": sum(1 for r in rows if is_unassigned(r)),
        "immediate_open": len(immediate_open),
        "high_open": len(high_open),
        "overdue_open": len(_overdue(rows, today)),
        "aged_open": {
            f">={aged_days}d": len(_aged_open(rows, aged_days)),
            ">=60d": len(_aged_open(rows, 60)),
            ">=90d": len(_aged_open(rows, 90)),
        },
        "bug_share_pct": round(100 * len(bugs) / total, 1) if total else 0,
        "by_priority": breakdown(rows, "Priority Name"),
        "by_task_type": breakdown(rows, "Task Name"),
        "by_aspl_status": breakdown(rows, "ASPL Status"),
        "top_clients_by_open": _top_open_by_group(rows, "Client Name"),
        "most_loaded_employees_by_open": _top_open_by_group(rows, "Employee Name"),
    }


def team_performance(rows, today, aged_days=30):
    """Per-employee accountability & workload balance, ranked by open load.
    Shows who is overloaded, who is behind (aged/overdue), and completion rate.
    """
    by_emp = {}
    for r in rows:
        emp = str(r.get("Employee Name") or "").strip()
        if emp:
            by_emp.setdefault(emp, []).append(r)

    people = []
    for emp, ers in by_emp.items():
        open_rows = _open(ers)
        people.append({
            "employee": emp,
            "total": len(ers),
            "open": len(open_rows),
            "closed": len(ers) - len(open_rows),
            "immediate_open": sum(
                1 for r in open_rows
                if str(r.get("Priority Name") or "").strip() == "Immediate"),
            "high_open": sum(
                1 for r in open_rows
                if str(r.get("Priority Name") or "").strip() == "High"),
            "aged_open": len(_aged_open(ers, aged_days)),
            "overdue_open": len(_overdue(ers, today)),
            "oldest_open_ageing_days": _oldest_ageing(open_rows),
            "completion_rate_pct": round(
                100 * (len(ers) - len(open_rows)) / len(ers), 1) if ers else 0,
        })
    people.sort(key=lambda p: (p["open"], p["aged_open"]), reverse=True)
    return {
        "note": ("'open' = ASPL Status not in a done state. The Employee Name "
                 "column occasionally holds a hospital name (see schema §4)."),
        "team_size": len(people),
        "employees": people,
    }


def clients_needing_attention(rows, today, aged_days=30, top=None):
    """Clients ranked by escalation risk: open + aged + immediate-open work."""
    by_client = {}
    for r in rows:
        c = str(r.get("Client Name") or "").strip()
        if c:
            by_client.setdefault(c, []).append(r)

    clients = []
    for c, crs in by_client.items():
        open_rows = _open(crs)
        immediate_open = sum(
            1 for r in open_rows
            if str(r.get("Priority Name") or "").strip() == "Immediate")
        aged_open = len(_aged_open(crs, aged_days))
        overdue_open = len(_overdue(crs, today))
        # Weighted risk: immediate hurts most, then aged/overdue, then raw open.
        risk = immediate_open * 3 + (aged_open + overdue_open) * 2 + len(open_rows)
        clients.append({
            "client": c,
            "total": len(crs),
            "open": len(open_rows),
            "immediate_open": immediate_open,
            "aged_open": aged_open,
            "overdue_open": overdue_open,
            "open_bugs": sum(
                1 for r in open_rows
                if str(r.get("Task Name") or "").strip() == "Bug"),
            "oldest_open_ageing_days": _oldest_ageing(open_rows),
            "risk_score": risk,
        })
    clients.sort(key=lambda x: x["risk_score"], reverse=True)
    return clients if top is None else clients[:top]


def priority_watch(rows, today, aged_days=30, worst=5):
    """Are the important things being handled? For Immediate & High: how much is
    open, unassigned, aged — plus the worst-offending open tickets."""
    result = {}
    for level in ("Immediate", "High"):
        subset = [r for r in rows
                  if str(r.get("Priority Name") or "").strip() == level]
        open_rows = _open(subset)
        open_rows_sorted = sorted(
            open_rows, key=lambda r: (ageing_of(r) or 0), reverse=True)
        result[level] = {
            "total": len(subset),
            "open": len(open_rows),
            "unassigned": sum(1 for r in subset if is_unassigned(r)),
            "aged_open": len(_aged_open(subset, aged_days)),
            "overdue_open": len(_overdue(subset, today)),
            "oldest_open_ageing_days": _oldest_ageing(open_rows),
            "worst_open_tickets": [
                {
                    "aspl": r.get("ASPL#"),
                    "client": r.get("Client Name"),
                    "employee": r.get("Employee Name"),
                    "status": r.get("ASPL Status"),
                    "ageing_days": ageing_of(r),
                    "description": (str(r.get("Description") or "")[:120]),
                }
                for r in open_rows_sorted[:worst]
            ],
        }
    return result


def sort_rows(rows, sort_by):
    """Sort ticket rows by a friendly key. Returns a new list."""
    if sort_by == "oldest":
        return sorted(rows, key=lambda r: r.get("sno", 0))
    if sort_by == "ageing":
        return sorted(rows, key=lambda r: (ageing_of(r) or 0), reverse=True)
    if sort_by == "priority":
        return sorted(rows, key=lambda r: PRIORITY_ORDER.get(
            str(r.get("Priority Name") or "").strip(), 99))
    # default: newest first
    return sorted(rows, key=lambda r: r.get("sno", 0), reverse=True)
