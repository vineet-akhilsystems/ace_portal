"""Leadership / people-support insights (pure functions over fetched rows).

These reframe the same ticket data around what a good manager actually does:
support people who are stuck or overloaded, recognise strong work, and fix
*systemic* problems (bad modules, unrealistic targets, slow triage) instead of
blaming individuals.

Data honesty: the view is a current snapshot, not a change history, so we can't
see reopens/reassignments. "Struggle" is inferred from the four timestamps
(Date -> Assigned On -> WorkedOn -> CompletedOn) and current status. `WorkedOn`
is sparsely populated (~14%), so pickup/effort signals are directional.
"""
from datetime import timedelta

from . import duplicates
from .aggregations import (
    ageing_of,
    breakdown,
    is_open,
    is_unassigned,
    parse_date,
    parse_datetime,
)

# Open states that mean "picked up / in flight" vs simply "not started yet".
IN_PROGRESS_STATES = {"Working", "Under Discussion", "Halt", "Pending", "Assigned"}


def _emp(r):
    return str(r.get("Employee Name") or "").strip()


def _dur_days(a_text, b_text):
    """Whole days from timestamp a_text to b_text, or None if either is missing
    or the order is inverted (bad data)."""
    a = parse_datetime(a_text)
    b = parse_datetime(b_text)
    if a is None or b is None:
        return None
    d = (b - a).total_seconds() / 86400.0
    return d if d >= 0 else None


def _res_days(r):
    """Resolution time (raised -> completed) in whole days, or None."""
    raised = parse_date(r.get("Date"))
    done = parse_date(r.get("CompletedOn"))
    if raised and done:
        d = (done - raised).days
        return d if d >= 0 else None
    return None


def _stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"count": n, "avg_days": round(sum(s) / n, 1),
            "median_days": round(median, 1),
            "min_days": round(s[0], 1), "max_days": round(s[-1], 1)}


# ======================================================================
# 1. SUPPORT & UNBLOCK
# ======================================================================
def stuck_tickets(rows, today, min_days=14, group_by="employee", limit=100):
    """Open tickets that have gone quiet for >= min_days — likely blocked, not
    forgotten. Highlights ones that were actively picked up (WorkedOn set) but
    never finished. Purpose: go unblock people."""
    stuck = [r for r in rows if is_open(r) and (ageing_of(r) or 0) >= min_days]
    stuck.sort(key=lambda r: (ageing_of(r) or 0), reverse=True)

    picked_up_stalled = [
        r for r in stuck
        if parse_datetime(r.get("WorkedOn")) and not parse_datetime(r.get("CompletedOn"))]

    grouped = None
    if group_by in ("employee", "client"):
        col = "Employee Name" if group_by == "employee" else "Client Name"
        by = {}
        for r in stuck:
            k = str(r.get(col) or "").strip()
            if not k:
                continue
            g = by.setdefault(k, {"stuck": 0, "in_progress_stalled": 0,
                                  "oldest_days": 0})
            g["stuck"] += 1
            if str(r.get("ASPL Status") or "").strip() in IN_PROGRESS_STATES:
                g["in_progress_stalled"] += 1
            g["oldest_days"] = max(g["oldest_days"], ageing_of(r) or 0)
        grouped = sorted(({"value": k, **v} for k, v in by.items()),
                         key=lambda x: (x["stuck"], x["oldest_days"]), reverse=True)

    return {
        "min_days": min_days,
        "note": ("'Stuck' = still-open ticket idle >= min_days. A stuck ticket "
                 "usually means the person is blocked (waiting on a client, a "
                 "decision, or another team) — an opportunity to help."),
        "total_stuck": len(stuck),
        "in_progress_but_stalled": len(picked_up_stalled),
        "by_status": breakdown(stuck, "ASPL Status"),
        "grouped_by": group_by if grouped is not None else None,
        "grouped": grouped,
        "worst": [{
            "aspl": r.get("ASPL#"), "client": r.get("Client Name"),
            "employee": _emp(r), "status": r.get("ASPL Status"),
            "priority": r.get("Priority Name"), "ageing_days": ageing_of(r),
            "description": str(r.get("Description") or "")[:120],
        } for r in stuck[:limit]],
    }


def workload_balance(rows, today, aged_days=30):
    """Is the load fair? Per-employee open/immediate/high/aged counts, plus a
    spread measure and explicit over/under-loaded flags. Also flags weekend/
    after-hours work where WorkedOn timestamps exist (a burnout signal).
    Purpose: rebalance and relieve, not rank."""
    by = {}
    for r in rows:
        e = _emp(r)
        if e:
            by.setdefault(e, []).append(r)

    people = []
    for e, ers in by.items():
        open_rows = [r for r in ers if is_open(r)]
        after_hours = 0
        for r in ers:
            wo = parse_datetime(r.get("WorkedOn"))
            if wo and (wo.weekday() >= 5 or wo.hour < 9 or wo.hour >= 19):
                after_hours += 1
        people.append({
            "employee": e,
            "open": len(open_rows),
            "immediate_open": sum(1 for r in open_rows
                                  if str(r.get("Priority Name") or "").strip() == "Immediate"),
            "high_open": sum(1 for r in open_rows
                             if str(r.get("Priority Name") or "").strip() == "High"),
            "aged_open": sum(1 for r in open_rows if (ageing_of(r) or 0) >= aged_days),
            "after_hours_worked": after_hours,
        })
    people.sort(key=lambda p: (p["open"], p["immediate_open"]), reverse=True)

    open_loads = [p["open"] for p in people] or [0]
    avg = sum(open_loads) / len(open_loads)
    hi = max(open_loads)
    lo = min(open_loads)
    overloaded = [p["employee"] for p in people
                  if p["open"] >= max(avg * 1.5, avg + 3) and p["open"] > 0]
    underloaded = [p["employee"] for p in people if p["open"] <= avg * 0.4]

    return {
        "note": ("Balance the open load. 'Employee Name' occasionally holds a "
                 "hospital/client name (schema quirk) — sanity-check outliers."),
        "team_size": len(people),
        "open_load": {"avg": round(avg, 1), "max": hi, "min": lo,
                      "imbalance_ratio": round(hi / avg, 1) if avg else None},
        "overloaded": overloaded,
        "underloaded_can_absorb": underloaded,
        "employees": people,
    }


def employee_briefing(emp_rows, all_rows, today, exact_name, aged_days=30):
    """A supportive 1:1 pack for ONE employee: current load, what's stuck, wins
    to recognise, missed targets (and whether their clients' targets are missed
    by everyone = not their fault), and module focus."""
    open_rows = [r for r in emp_rows if is_open(r)]

    # stuck
    stuck = sorted([r for r in open_rows if (ageing_of(r) or 0) >= aged_days],
                   key=lambda r: (ageing_of(r) or 0), reverse=True)
    # wins: recently completed + fastest resolutions
    res = [(r, _res_days(r)) for r in emp_rows]
    res = [(r, d) for r, d in res if d is not None]
    fastest = sorted(res, key=lambda rd: rd[1])[:5]
    immediate_done = sum(1 for r in emp_rows
                         if not is_open(r)
                         and str(r.get("Priority Name") or "").strip() == "Immediate")
    # missed targets for this employee
    missed = []
    for r in open_rows:
        d = parse_date(r.get("Target Date"))
        if d is not None and d < today:
            missed.append(r)
    # are those clients' targets missed by EVERYONE? (defend the employee)
    emp_clients = {str(r.get("Client Name") or "").strip() for r in missed}
    systemic = []
    for c in emp_clients:
        crows = [r for r in all_rows
                 if str(r.get("Client Name") or "").strip() == c and is_open(r)]
        cm = sum(1 for r in crows
                 if (pd := parse_date(r.get("Target Date"))) and pd < today)
        distinct_emps = len({_emp(r) for r in crows
                             if (pd := parse_date(r.get("Target Date"))) and pd < today})
        if cm >= 3 and distinct_emps >= 2:
            systemic.append({"client": c, "overdue_open": cm,
                             "employees_affected": distinct_emps})

    return {
        "employee": exact_name,
        "load": {
            "total": len(emp_rows), "open": len(open_rows),
            "immediate_open": sum(1 for r in open_rows
                                  if str(r.get("Priority Name") or "").strip() == "Immediate"),
            "by_status": breakdown(open_rows, "ASPL Status"),
        },
        "needs_help_stuck": [{
            "aspl": r.get("ASPL#"), "client": r.get("Client Name"),
            "status": r.get("ASPL Status"), "ageing_days": ageing_of(r),
            "description": str(r.get("Description") or "")[:110],
        } for r in stuck[:8]],
        "wins_to_recognise": {
            "completed": len(emp_rows) - len(open_rows),
            "immediate_resolved": immediate_done,
            "fastest_resolutions": [{
                "aspl": r.get("ASPL#"), "days": d,
                "description": str(r.get("Description") or "")[:80],
            } for r, d in fastest],
        },
        "missed_targets_open": len(missed),
        "likely_not_their_fault": {
            "hint": ("These clients' deadlines are being missed by multiple "
                     "people — likely unrealistic targets, not this employee."),
            "clients": systemic,
        },
        "focus_modules": breakdown(emp_rows, "Module Name")[:5],
    }


# ======================================================================
# 2. FIX SYSTEMIC PROBLEMS
# ======================================================================
def problem_hotspots(rows, today, group_by="module", top=10):
    """Which module/client keeps generating pain: volume, open, bug share,
    overdue rate, and rework (share of tickets that are near-duplicates of
    others). Purpose: fix the system, not the person."""
    col = "Module Name" if group_by == "module" else "Client Name"

    # rework: which ASPL#s land in a duplicate cluster (global)
    dup = duplicates.find_similar(rows, min_similarity=0.5, min_words=4,
                                  max_clusters=10_000)
    dup_aspls = {t["aspl"] for c in dup["clusters"] for t in c["tickets"]}

    by = {}
    for r in rows:
        k = str(r.get(col) or "").strip()
        if k:
            by.setdefault(k, []).append(r)

    out = []
    for k, grp in by.items():
        open_rows = [r for r in grp if is_open(r)]
        bugs = sum(1 for r in grp if str(r.get("Task Name") or "").strip() == "Bug")
        overdue = sum(1 for r in open_rows
                      if (d := parse_date(r.get("Target Date"))) and d < today)
        rework = sum(1 for r in grp if r.get("ASPL#") in dup_aspls)
        total = len(grp)
        # pain score: volume-weighted mix of bug share, overdue rate, rework.
        pain = round(open_rows.__len__()
                     + bugs * 0.5
                     + overdue * 2
                     + rework * 1.5, 1)
        out.append({
            "value": k, "total": total, "open": len(open_rows),
            "bug_share_pct": round(100 * bugs / total, 1) if total else 0,
            "overdue_open": overdue,
            "rework_tickets": rework,
            "rework_pct": round(100 * rework / total, 1) if total else 0,
            "oldest_open_ageing_days": max((ageing_of(r) or 0 for r in open_rows),
                                           default=0),
            "pain_score": pain,
        })
    out.sort(key=lambda x: x["pain_score"], reverse=True)
    return {"grouped_by": group_by,
            "note": "rework = tickets that near-duplicate another (wasted effort).",
            "hotspots": out[:top]}


def target_realism(rows, today, group_by="client", min_tickets=5, top=15):
    """Where targets are missed by MANY people/tickets — a sign the deadlines
    are unrealistic, not that individuals are failing. Defends the team."""
    col = {"client": "Client Name", "module": "Module Name",
           "employee": "Employee Name"}.get(group_by)
    if col is None:
        raise ValueError("group_by must be client, module or employee")
    by = {}
    for r in rows:
        if not is_open(r):
            continue
        d = parse_date(r.get("Target Date"))
        if d is None:
            continue
        k = str(r.get(col) or "").strip()
        if k:
            by.setdefault(k, []).append((r, d))

    out = []
    for k, items in by.items():
        if len(items) < min_tickets:
            continue
        missed = [(r, d) for r, d in items if d < today]
        emps = len({_emp(r) for r, _ in missed})
        out.append({
            "value": k,
            "open_with_target": len(items),
            "missed": len(missed),
            "miss_rate_pct": round(100 * len(missed) / len(items), 1),
            "employees_affected": emps,
        })
    # unrealistic = high miss rate AND spread across multiple people
    out.sort(key=lambda x: (x["miss_rate_pct"], x["employees_affected"]),
             reverse=True)
    return {
        "grouped_by": group_by,
        "min_tickets": min_tickets,
        "note": ("High miss_rate across several employees_affected suggests the "
                 "target dates are unrealistic — a planning fix, not a people "
                 "problem."),
        "groups": out[:top],
    }


def cycle_time_breakdown(rows, group_by="none", top=15):
    """Split the pipeline into stages to find WHERE work stalls:
      triage   = raised -> assigned
      pickup   = assigned -> worked
      execution= worked -> completed
      total    = raised -> completed
    Each stage reports its own coverage (WorkedOn is sparse)."""
    def stages_for(subset):
        triage, pickup, execu, total = [], [], [], []
        for r in subset:
            t = _dur_days(r.get("Date"), r.get("Assigned On"))
            if t is not None:
                triage.append(t)
            p = _dur_days(r.get("Assigned On"), r.get("WorkedOn"))
            if p is not None:
                pickup.append(p)
            e = _dur_days(r.get("WorkedOn"), r.get("CompletedOn"))
            if e is not None:
                execu.append(e)
            tot = _res_days(r)
            if tot is not None:
                total.append(tot)
        return {"triage_raised_to_assigned": _stats(triage),
                "pickup_assigned_to_worked": _stats(pickup),
                "execution_worked_to_completed": _stats(execu),
                "total_raised_to_completed": _stats(total)}

    result = {
        "note": ("Slow 'triage' or 'pickup' is a process/queue problem, not an "
                 "employee one. WorkedOn is sparse so pickup/execution counts "
                 "are lower — read the per-stage 'count'."),
        "overall": stages_for(rows),
    }
    if group_by != "none":
        col = {"employee": "Employee Name", "client": "Client Name",
               "module": "Module Name"}.get(group_by)
        if col is None:
            raise ValueError("group_by must be none, employee, client or module")
        by = {}
        for r in rows:
            k = str(r.get(col) or "").strip()
            if k:
                by.setdefault(k, []).append(r)
        groups = [{"value": k, **stages_for(v)} for k, v in by.items()]
        # rank by slowest total-average that actually has data
        groups.sort(
            key=lambda g: (g["total_raised_to_completed"] or {}).get("avg_days", 0),
            reverse=True)
        result["by_" + group_by] = groups[:top]
    return result


# ======================================================================
# 3. RECOGNISE & GROW
# ======================================================================
def recognition(rows, today, since=None, until=None, top=8):
    """The positive spotlight: who to thank / promote. Top closers, fastest
    average resolvers, most Immediate fires handled, best target adherence —
    over an optional completed-in window."""
    lo = parse_date(since) if since else None
    hi = parse_date(until) if until else None

    def in_window(r):
        if lo is None and hi is None:
            return True
        d = parse_date(r.get("CompletedOn"))
        if d is None:
            return False
        if lo and d < lo:
            return False
        if hi and d > hi:
            return False
        return True

    by = {}
    for r in rows:
        e = _emp(r)
        if e:
            by.setdefault(e, []).append(r)

    closers, speed, firefighters, adherence = [], [], [], []
    for e, ers in by.items():
        completed = [r for r in ers if not is_open(r) and in_window(r)]
        res = [d for r in completed if (d := _res_days(r)) is not None]
        immediate_done = sum(1 for r in completed
                             if str(r.get("Priority Name") or "").strip() == "Immediate")
        # target adherence: completed on/before target
        on_time = tot = 0
        for r in completed:
            tgt = parse_date(r.get("Target Date"))
            done = parse_date(r.get("CompletedOn"))
            if tgt and done:
                tot += 1
                if done <= tgt:
                    on_time += 1
        if completed:
            closers.append((e, len(completed)))
        if len(res) >= 3:
            speed.append((e, round(sum(res) / len(res), 1), len(res)))
        if immediate_done:
            firefighters.append((e, immediate_done))
        if tot >= 3:
            adherence.append((e, round(100 * on_time / tot, 1), tot))

    closers.sort(key=lambda x: x[1], reverse=True)
    speed.sort(key=lambda x: x[1])  # lower avg days = faster
    firefighters.sort(key=lambda x: x[1], reverse=True)
    adherence.sort(key=lambda x: x[1], reverse=True)

    return {
        "period": ({"since": since, "until": until} if (since or until)
                   else "all-time"),
        "note": "Recognition, not ranking — surface people to appreciate.",
        "top_closers": [{"employee": e, "completed": n} for e, n in closers[:top]],
        "fastest_avg_resolution": [
            {"employee": e, "avg_days": a, "tickets": n} for e, a, n in speed[:top]],
        "most_immediate_handled": [
            {"employee": e, "immediate_completed": n} for e, n in firefighters[:top]],
        "best_target_adherence": [
            {"employee": e, "on_time_pct": p, "tickets": n} for e, p, n in adherence[:top]],
    }


def expertise_map(rows, by="module", top=12):
    """Who is the go-to specialist for each module/client (volume + completion),
    plus BUS-FACTOR risks: areas where essentially one person holds the
    knowledge (fragile, and often a silently-overloaded person)."""
    col = "Module Name" if by == "module" else "Client Name"
    area = {}
    for r in rows:
        k = str(r.get(col) or "").strip()
        e = _emp(r)
        if k and e:
            area.setdefault(k, {}).setdefault(e, []).append(r)

    areas_out, bus_risks = [], []
    for k, emps in area.items():
        total = sum(len(v) for v in emps.values())
        ranked = sorted(emps.items(), key=lambda kv: len(kv[1]), reverse=True)
        handlers = [{
            "employee": e,
            "tickets": len(v),
            "share_pct": round(100 * len(v) / total, 1) if total else 0,
            "completion_rate_pct": round(
                100 * sum(1 for r in v if not is_open(r)) / len(v), 1) if v else 0,
        } for e, v in ranked[:5]]
        top_share = handlers[0]["share_pct"] if handlers else 0
        bus = len(emps) <= 1 or (top_share >= 80 and total >= 4)
        areas_out.append({"area": k, "total": total,
                          "distinct_handlers": len(emps),
                          "specialists": handlers, "bus_factor_risk": bus})
        if bus:
            bus_risks.append({"area": k, "total": total,
                              "distinct_handlers": len(emps),
                              "main_person": handlers[0]["employee"] if handlers else None})

    areas_out.sort(key=lambda a: a["total"], reverse=True)
    bus_risks.sort(key=lambda b: b["total"], reverse=True)
    return {
        "by": by,
        "note": ("bus_factor_risk = knowledge concentrated in one person — a "
                 "fragility to de-risk (cross-train) and often a person to "
                 "relieve."),
        "areas": areas_out[:top],
        "bus_factor_risks": bus_risks,
    }


# ======================================================================
# 4. FLOW & BACKLOG HEALTH
# ======================================================================
def backlog_health(rows, today, period="month", last_n=6):
    """Are we keeping up? Intake (raised) vs throughput (completed) per period,
    net change, and the current open backlog with its aged share."""
    def series(col):
        b = {}
        for r in rows:
            d = parse_date(r.get(col))
            if d is None:
                continue
            key = (f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
                   if period == "week" else f"{d.year}-{d.month:02d}")
            b[key] = b.get(key, 0) + 1
        return b

    raised = series("Date")
    completed = series("CompletedOn")
    keys = sorted(set(raised) | set(completed))
    if last_n and last_n > 0:
        keys = keys[-last_n:]
    flow = [{"bucket": k, "raised": raised.get(k, 0),
             "completed": completed.get(k, 0),
             "net_change": raised.get(k, 0) - completed.get(k, 0)} for k in keys]

    open_rows = [r for r in rows if is_open(r)]
    aged = sum(1 for r in open_rows if (ageing_of(r) or 0) >= 30)
    return {
        "period": period,
        "note": ("net_change > 0 means the backlog grew that period (more came "
                 "in than went out). Watch the trend, not one bucket."),
        "current_open_backlog": len(open_rows),
        "aged_30d_plus": aged,
        "flow": flow,
    }


def triage_gaps(rows, today, slow_days=3, limit=50):
    """Where the intake process fails people: unassigned Immediate/High tickets,
    and tickets that sat a long time between raised and assigned."""
    unassigned_hot = [r for r in rows
                      if is_unassigned(r)
                      and str(r.get("Priority Name") or "").strip() in ("Immediate", "High")]

    slow = []
    for r in rows:
        lag = _dur_days(r.get("Date"), r.get("Assigned On"))
        if lag is not None and lag >= slow_days:
            slow.append((r, round(lag, 1)))
    slow.sort(key=lambda rl: rl[1], reverse=True)

    return {
        "note": ("Slow/absent triage is a queue problem the front office owns — "
                 "not the assignee's fault. Fix the intake, help the team."),
        "unassigned_immediate_or_high": len(unassigned_hot),
        "unassigned_list": [{
            "aspl": r.get("ASPL#"), "client": r.get("Client Name"),
            "priority": r.get("Priority Name"), "ageing_days": ageing_of(r),
            "description": str(r.get("Description") or "")[:100],
        } for r in unassigned_hot[:limit]],
        "slow_triage_days_threshold": slow_days,
        "slow_triage_count": len(slow),
        "slowest_triage": [{
            "aspl": r.get("ASPL#"), "client": r.get("Client Name"),
            "raised": r.get("Date"), "assigned": r.get("Assigned On"),
            "triage_lag_days": lag,
        } for r, lag in slow[:limit]],
    }
