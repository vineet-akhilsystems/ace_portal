# `vwemployeerptclientforsanjaysir` — View Reference

> Database: **Akhil_Reporting** on `43.242.214.195`
> A client/task reporting view. ~482 rows (current sample). Each row = one
> **ASPL task** (bug / requirement / activity) raised for a client, with its
> status, assignee, priority, dates and ageing.
>
> ⚠️ **Schema change (2026-07-28):** the view swapped `Reported By` for a new
> `WorkedOn` column — see §1 and the gotcha in §4. `ace/tools.py` and
> `ace/aggregations.py` still reference `Reported By`; that filter/group-by is
> now silently dead (matches nothing) until the code is updated.

---

## 1. All 23 columns

| # | Column | Type | Nullable | What it holds |
|---|--------|------|----------|---------------|
| 1 | `ClientId` | int | no | Internal client id |
| 2 | `Client Name` | varchar(100) | no | Client / hospital name |
| 3 | `Module Name` | varchar(100) | yes | Product module (e.g. Augastam EMR) |
| 4 | `ASPL#` | varchar(50) | no | ASPL ticket id (e.g. `ASPL-165661`) |
| 5 | `Client#` | varchar(50) | no | Client-side ticket ref (often blank) |
| 6 | `Date` | varchar(10) | yes | Raised date — **stored as text `dd/MM/yyyy`** |
| 7 | `Description` | nvarchar | yes | Free-text task description |
| 8 | `Task Name` | varchar(100) | no | Task **type**: Bug, New Requirement, … |
| 9 | `Client Status` | varchar(100) | yes | Status from client's side: Open/Done/Cancel |
| 10 | `ASPL Status` | varchar(100) | yes | Internal delivery status (12 values) |
| 11 | `Employee Name` | varchar(202) | yes | Assigned employee |
| 12 | `Aspl Datetime` | varchar(10) | yes | Internal timestamp — **text** |
| 13 | `Target Date` | varchar(10) | yes | Due date — **text** |
| 14 | `Task AssignBy` | varchar(202) | yes | Who assigned the task |
| 15 | `Assigned On` | varchar(16) | yes | Assignment timestamp — **text** |
| 16 | `WorkedOn` | varchar(16) | yes | 🆕 Timestamp task was picked up/worked — **text**. Sparse: only ~66/482 rows populated |
| 17 | `CompletedBy` | varchar(202) | yes | Who completed it |
| 18 | `CompletedOn` | varchar(16) | yes | Completion date — **text** |
| 19 | `Priority Name` | varchar(100) | no | Immediate / High / Medium / Low |
| 20 | `Lead Name` | varchar(202) | yes | Team lead (almost all = Satender Datt Chamoli) |
| 21 | `Agening` | int | yes | **Ageing in days** (note: spelled "Agening") |
| 22 | `ModuleProject` | varchar(100) | yes | Sub-project tag (mostly blank) |
| 23 | `sno` | bigint | no | Sort key — higher = newer. Primary ordering |

> **Removed:** `Reported By` (varchar(200)) no longer exists in the view — it
> was replaced by `WorkedOn` in this refresh. Any code/report expecting
> `Reported By` needs to be updated (see callout above).

> ⚠️ **All dates are stored as text**, not real `date`/`datetime` columns. Any
> date filtering/sorting must parse `dd/MM/yyyy` (or `dd/MM/yyyy HH:mm:ss`)
> first. See note in §4.

---

## 2. The categorical columns (what values actually exist)

These are the columns worth building **filters, group-bys, and dropdowns**
around. Counts are from the current ~482-row sample.

### `Task Name` — the task type (8 values)
`Bug` (274) · `New Requirement` (105) · `Change/Enhancement` (47) · `Activity` (27) · `Integration` (14) · `Functional` (8) · `Publish` (4) · `Query` (3)

### `ASPL Status` — internal delivery status (12 values)
`Delivered` (178) · `Open` (88) · `Verified` (71) · `Completed` (54) · `Delivered On UAT` (36) · `Working` (23) · `Cancel` (6) · `Pending` (4) · `Not Feasible` (3) · `Under Discussion` (2) · `Halt` (2) · `Assigned` (1)

### `Client Status` — client-side status (3 values)
`Open` (76) · `Done` (74) · `Cancel` (7)

### `Priority Name` — priority (4 values)
`High` (266) · `Immediate` (150) · `Medium` (40) · `Low` (26)

### `Module Name` — product module (8 values)
`Augastam EMR` (436) · `Augastam - Billing` (24) · `Augastam - Administration` (9) · `Augastam - Registration` (4) · `Administration` (3) · `Ward (Clinical)` (3) · `Interface` (2) · `ACE Portal` (1)

### `Client Name` — 32 distinct clients. Top ones:
Augastam (New Product) (139) · Yashoda Medicity,Indirapuram (69) · Ruby General Hospital (34) · Eye 7 Hospital (32) · Bharati Vidyapeeth University, Pune (27) · GUT GI (25) · Mandani Hospital (24) · Child Trust (20) · Pacific Health One Hospital (16) · Evercare Hyderabad (16) …

### `Employee Name` — 37 distinct assignees. Top ones:
Yashoda Medicity Hospital Indirapuram (51) · Siddharth Sharma (47) · Ravi Singh (39) · Kuldeep Kumar (37) · Satender Datt Chamoli (36) · Disha Bakshi (36) · Amit Kumar Gupta (32) · Swadhin Kumar Senapati (27) · Shivam Gupta (23) …

### `Lead Name` — 5 values (heavily skewed to one lead)
Satender Datt Chamoli (473) · Ashutosh Sharma (3) · Surya Pratap Singh (3) · Santosh Kumar (2) · Manoj Kumar Puri (1)

### `WorkedOn` — 🆕 not categorical, it's a text timestamp (`dd/MM/yyyy HH:mm`)
Only ~66 of 482 rows (~14%) have a value — looks like it's stamped when a task
moves into `Working`, so most historical/older rows are blank. Treat like
`Assigned On` / `CompletedOn` for parsing purposes, not like a dropdown filter.

> **Removed:** the `Reported By` breakdown (26 distinct reporters) no longer
> applies — the column is gone from the view.

---

## 3. What questions users can ask (and the tools that answer them)

Grouped by the kind of MCP tool that would serve them.

### A. Filtered lookups — `search_report(...)` *(already built)*
- "Show all **open bugs** for **Yashoda**."
- "List **immediate priority** tasks assigned to **Siddharth Sharma**."
- "Show everything for **Ruby General Hospital**."

→ Filters on: Client Name, Employee Name, Task Name, Priority, ASPL Status,
   Client Status. (Reported By filter is currently dead — see callout in §1.)

### B. Counts & breakdowns — `count_by(column)` *(suggested new tool)*
- "How many tasks are there **per client**?"
- "Break down tasks by **ASPL status**."
- "How many **bugs vs new requirements**?"
- "Count of tasks **per employee**."
- "How many **immediate** priority items are open?"

→ A single `count_by("ASPL Status")` style tool covers dozens of questions.

### C. Ageing / SLA — uses `Agening` + `Target Date` *(suggested new tool)*
- "Which tasks are **ageing more than 30 days**?"
- "Show the **oldest open bugs**."
- "What's **overdue** (past target date)?"
- "Average ageing **per employee** / **per client**."

→ `overdue_or_aged(min_days=30)` and/or sort by `Agening DESC`.

### D. Per-person / per-client dashboards *(suggested new tool)*
- "Give me **Satender's workload** — open vs delivered vs verified."
- "**Client scorecard** for Eye 7: totals by status & priority."
- "Who has the **most open immediate** tasks right now?"

→ `workload(employee_name)` and `client_summary(client_name)` — each returns a
   small status/priority breakdown.

### E. Recent activity — ordered by `sno`/dates *(partly built)*
- "**Latest 10** tasks raised."
- "What was **completed this week**?" *(needs date parsing — see §4)*
- "Show tasks **assigned today**."

### F. Free-text search — on `Description`
- "Find tasks mentioning **'follow-up appointment'**."
- "Any tasks about **radiology reports not showing**?"

→ `search_description(text)` → `WHERE Description LIKE '%text%'`.

---

## 4. Gotchas to design around

1. **Dates are text (`dd/MM/yyyy`).** To filter by real date ranges, parse with
   `TRY_CONVERT(date, [Date], 103)` in SQL (103 = British `dd/MM/yyyy`). Sorting
   the raw text will sort wrong (lexical, not chronological). Prefer `sno` for
   "newest first".
2. **`Agening` is misspelled** but is the ageing-in-days integer — good for
   numeric filters/sorts.
3. **`Employee Name` sometimes contains a hospital name** (e.g. "EYE 7
   Hospital") — the view mixes staff and client entries in that column. Don't
   assume it's always a person.
4. **The view is expensive to run.** It recomputes on every query, so
   `COUNT(DISTINCT)` across all columns is slow. Cache/limit where possible;
   fetch once and aggregate in Python for multi-column breakdowns.
5. **`Lead Name` is ~98% one person** — low value as a filter.
6. **`Reported By` was removed, `WorkedOn` was added.** `ace/tools.py`
   (`search_tickets`'s `reported_by` param) and `ace/aggregations.py`
   (`COUNTABLE` set) still reference `Reported By`. Since rows are plain
   dicts, `row.get("Reported By")` just returns `None` for every row now —
   no crash, but the filter/group-by silently matches nothing. Needs a code
   fix to drop `Reported By` and optionally expose `WorkedOn`.
7. **`WorkedOn` is text (`dd/MM/yyyy HH:mm`) and mostly blank** (~14%
   populated in the current sample) — parse like the other date columns, and
   don't treat missing as an error.

---

## 5. The implemented tool set (30 tools)

**Primitives** — compose almost any question:

| Tool | Answers |
|------|---------|
| `search_tickets(...)` ✅ | Any filtered listing: by client, employee, task_type, priority, aspl_status, client_status, module, reported_by ⚠️ *(dead — column removed, see §1/§4)*, completed_by, assigned_by, description text; `only_open`, `overdue`, date windows; sort + limit. |
| `count_by(column, second_dimension, only_open, avg_ageing)` ✅ | Counts, cross-tabs, average ageing per group. |
| `get_ticket(aspl_number)` ✅ | One ticket by ASPL#. |

**Single-entity dashboards:**

| Tool | Answers |
|------|---------|
| `get_employee_client_report(limit)` ✅ | "latest N tasks" |
| `get_report_row_count()` ✅ | "how many total" |
| `overdue_or_aged(min_days, limit)` ✅ | ageing/SLA (C) |
| `workload(employee_name)` ✅ | per-person deep-dive (D) |
| `client_summary(client_name)` ✅ | per-client deep-dive (D) |
| `search_description(text, limit)` ✅ | free-text search (F) |
| `refresh_data()` ✅ | force a fresh pull from the DB |

**Executive / management insights (CEO / CTO / PM):**

| Tool | Answers |
|------|---------|
| `executive_summary(aged_days)` ✅ | Portfolio health: open/closed, unassigned, immediate/high open, overdue & aged, bug share, top clients/employees by open load. |
| `team_performance(aged_days)` ✅ | Who's doing what, who's overloaded/behind — per-employee open, aged, overdue, oldest item, completion rate, ranked. |
| `clients_needing_attention(aged_days, top)` ✅ | Clients ranked by escalation risk. |
| `priority_watch(aged_days)` ✅ | Is Immediate/High work being handled — open, unassigned, aged + worst offenders. |

**Deadlines / duplicates / trends / speed (added 2026-07-29):**

| Tool | Answers |
|------|---------|
| `target_date_status(only_open, due_soon_days, group_by)` ✅ | Tickets vs their Target Date: missed / due_soon / upcoming / no_target. "How many **employees haven't met their target dates**?" → `employees_with_missed_targets` + names. Grouped by employee or client, with worst-overdue per group. |
| `find_similar_tickets(min_similarity, same_client_only, min_words, max_clusters)` ✅ | **Duplicate / near-duplicate** detection — the same issue raised as separate ASPL tickets, even reworded. Word-set (Jaccard) similarity + clustering. |
| `activity_trend(date_field, period, last_n)` ✅ | Ticket **volume over time** (raised/completed/assigned/target) per week or month. "How many closed this month vs last?" |
| `resolution_time(group_by, top)` ✅ | **How fast** tickets resolve = CompletedOn − Date(raised), in days. Overall + per employee/client/priority/task_type (slowest first). Reports `coverage_pct` (only tickets with both dates). |

**Leadership / people-support (added 2026-07-29)** — reframes the data around
*supporting* people and fixing the *system*, not policing individuals. Pure
logic lives in `ace/leadership.py`. Data honesty: the view is a snapshot (no
change history → no reopen/reassignment tracking); `WorkedOn` is ~14% filled so
pickup/effort signals are directional; and `Employee Name` sometimes holds a
hospital name (schema §4) so employee rankings can include non-person rows.

| Tool | Answers / purpose |
|------|-------------------|
| `stuck_tickets(min_days, group_by, limit)` ✅ | Open tickets idle too long → someone is likely **blocked**. "Who do I need to unblock?" |
| `workload_balance(aged_days)` ✅ | Is the load **fair**? Per-person open/immediate/aged + spread + over/under-loaded lists + after-hours flag. For rebalancing/relief. |
| `employee_briefing(employee_name, aged_days)` ✅ | A supportive **1:1 pack** for one person: load, what's stuck, wins to recognise, missed targets, and whether those are systemic (not their fault). |
| `problem_hotspots(group_by, top)` ✅ | Which **module/client keeps breaking** — pain score from open, bug share, overdue, and rework (duplicate) density. Fix the system. |
| `target_realism(group_by, min_tickets, top)` ✅ | Where deadlines are missed by **many people** → targets are unrealistic, not individuals failing. Defends the team. |
| `cycle_time_breakdown(group_by, top)` ✅ | **Where work stalls**: triage (raised→assigned), pickup (assigned→worked), execution (worked→completed). Process fix, not people. |
| `recognition(since, until, top)` ✅ | The **positive spotlight** — top closers, fastest resolvers, most Immediate handled, best target adherence. Who to thank/promote. |
| `expertise_map(by, top)` ✅ | Go-to **specialists** per module/client + **bus-factor risk** (knowledge stuck in one person). Assignment, mentoring, cross-training. |
| `backlog_health(period, last_n)` ✅ | **Intake vs throughput** per period + net change + current open backlog. Are we keeping up? |
| `triage_gaps(slow_days, limit)` ✅ | Unassigned Immediate/High + slow raised→assigned tickets. The **intake process** failing people. |

✅ = implemented & tested against the live DB.

**Definitions used by the insight tools:**
- **Open** = ASPL Status not in {Delivered, Verified, Completed, Delivered On
  UAT, Cancel, Not Feasible}. Everything else = open/active.
- **Aged** = open AND `Agening` ≥ threshold (default 30 days).
- **Overdue** = open AND Target Date < today (parsed from `dd/MM/yyyy`).

All tools read from a single in-memory snapshot of the view (fetched once, ~35s,
then cached for `DB_CACHE_TTL` seconds) — because the view is slow and the link
is flaky. See README → *Caching*.

---

## 6. Executive questions this now answers

| A leader asks… | Tool |
|---|---|
| "Give me the overall status of delivery." | `executive_summary()` |
| "How much immediate work is still open and how old is the worst?" | `priority_watch()` |
| "Who on the team is overloaded or falling behind?" | `team_performance()` |
| "Which clients are at risk of escalating?" | `clients_needing_attention()` |
| "What's overdue right now?" | `search_tickets(overdue=True)` |
| "Show all open immediate bugs for Yashoda." | `search_tickets(client_name='Yashoda', priority='Immediate', task_type='Bug', only_open=True)` |
| "Priority vs status matrix." | `count_by('Priority Name', second_dimension='ASPL Status')` |
| "Average ageing per priority." | `count_by('Priority Name', avg_ageing=True)` |
| "What was raised in the last 7 days?" | `search_tickets(date_field='raised', last_days=7)` |
| "Details of ASPL-165661." | `get_ticket('165661')` |
| "How many employees haven't met their target dates yet?" | `target_date_status()` |
| "What's overdue / due this week, and who owns it?" | `target_date_status(group_by='employee')` |
| "Find duplicate or repeated tickets (same issue, worded differently)." | `find_similar_tickets(min_similarity=0.5)` |
| "How many tickets did we close each month?" | `activity_trend(date_field='completed', period='month')` |
| "How long do tickets take to resolve, by priority?" | `resolution_time(group_by='priority')` |
