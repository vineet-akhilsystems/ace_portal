"""Offline unit tests for the pure helpers + insights (no DB needed).

Run:  py -m pytest tests/   (or: py tests/test_aggregations.py)
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ace.aggregations import (  # noqa: E402
    apply_filters, breakdown, confident_fuzzy_match, contains, is_open,
    parse_date, resolve_employee,
)
from ace import insights  # noqa: E402

NAME_ROWS = [{"Employee Name": n} for n in [
    "Siddharth Sharma", "Ravi Singh", "Kuldeep Kumar", "Namit Kumar",
    "Amit Kumar Gupta", "Disha Bakshi",
]]

TODAY = date(2026, 7, 24)

SAMPLE = [
    {"ASPL#": "ASPL-1", "Client Name": "Eye 7", "Task Name": "Bug",
     "Priority Name": "Immediate", "ASPL Status": "Open",
     "Employee Name": "Ravi", "Agening": 40, "Target Date": "01/01/2026", "sno": 4},
    {"ASPL#": "ASPL-2", "Client Name": "Eye 7", "Task Name": "Bug",
     "Priority Name": "High", "ASPL Status": "Delivered",
     "Employee Name": "Ravi", "Agening": 5, "Target Date": "31/12/2026", "sno": 3},
    {"ASPL#": "ASPL-3", "Client Name": "Ruby", "Task Name": "New Requirement",
     "Priority Name": "Low", "ASPL Status": "Not Assigned",
     "Employee Name": "Disha", "Agening": None, "Target Date": "", "sno": 2},
    {"ASPL#": "ASPL-4", "Client Name": "Ruby", "Task Name": "",
     "Priority Name": "High", "ASPL Status": "Working",
     "Employee Name": "Disha", "Agening": 90, "Target Date": "01/06/2026", "sno": 1},
]


def test_contains_case_insensitive():
    assert contains("Siddharth Sharma", "sharma")
    assert not contains("Siddharth Sharma", "verma")
    assert not contains(None, "x")


def test_breakdown_skips_empty_and_orders():
    result = breakdown(SAMPLE, "Task Name")
    assert result[0] == {"value": "Bug", "count": 2}
    assert all(r["value"] != "" for r in result)


def test_is_open():
    assert is_open({"ASPL Status": "Open"})
    assert is_open({"ASPL Status": "Working"})
    assert not is_open({"ASPL Status": "Delivered"})
    assert not is_open({"ASPL Status": "Verified"})


def test_parse_date():
    assert parse_date("24/07/2026") == date(2026, 7, 24)
    assert parse_date("24/07/2026 11:51:00") == date(2026, 7, 24)
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("not a date") is None


def test_apply_filters_combines_and():
    out = apply_filters(SAMPLE, {"client_name": "Eye 7", "task_type": "Bug"})
    assert len(out) == 2
    out2 = apply_filters(SAMPLE, {"priority": "High"})
    assert len(out2) == 2


def test_executive_summary_counts_open_and_overdue():
    s = insights.executive_summary(SAMPLE, TODAY, aged_days=30)
    assert s["total_tickets"] == 4
    # Open = Open, Not Assigned, Working (3); Delivered is closed.
    assert s["open"] == 3
    assert s["unassigned"] == 1
    assert s["immediate_open"] == 1
    # ASPL-1 target 01/01/2026 past & open -> overdue; ASPL-4 target 01/06/2026 past & open.
    assert s["overdue_open"] == 2
    assert s["aged_open"][">=30d"] == 2  # ageing 40 and 90, both open


def test_team_performance_ranks_by_open():
    t = insights.team_performance(SAMPLE, TODAY)
    assert t["team_size"] == 2
    names = [p["employee"] for p in t["employees"]]
    assert set(names) == {"Ravi", "Disha"}
    disha = next(p for p in t["employees"] if p["employee"] == "Disha")
    assert disha["open"] == 2  # Not Assigned + Working


def test_clients_needing_attention_risk_order():
    clients = insights.clients_needing_attention(SAMPLE, TODAY, aged_days=30)
    assert clients[0]["client"] in {"Eye 7", "Ruby"}
    assert all("risk_score" in c for c in clients)


def test_resolve_substring_first_and_last_name():
    # first name, last name, and prefix all resolve via Tier-1 substring
    m, s = resolve_employee(NAME_ROWS, "siddharth")
    assert m == ["Siddharth Sharma"] and s == []
    m, _ = resolve_employee(NAME_ROWS, "sharma")
    assert m == ["Siddharth Sharma"]
    m, _ = resolve_employee(NAME_ROWS, "sid")
    assert m == ["Siddharth Sharma"]


def test_resolve_ambiguous_common_surname_not_merged():
    # 'Kumar' legitimately matches several different people -> all returned
    m, s = resolve_employee(NAME_ROWS, "Kumar")
    assert set(m) == {"Kuldeep Kumar", "Namit Kumar", "Amit Kumar Gupta"}
    assert s == []


def test_resolve_typo_first_name_suggests():
    # misspelled first name has no substring match -> fuzzy suggestion
    m, s = resolve_employee(NAME_ROWS, "Sidharth")
    assert m == []
    assert "Siddharth Sharma" in s


def test_resolve_typo_last_name_suggests():
    m, s = resolve_employee(NAME_ROWS, "Kuldeep Kumr")
    assert m == []
    assert s and s[0] == "Kuldeep Kumar"


def test_resolve_garbage_rejected():
    m, s = resolve_employee(NAME_ROWS, "zxcvbnm")
    assert m == [] and s == []


def test_confident_match_auto_resolves_clear_typo():
    # 'kuldeep kumr' has one clear winner and no close rival -> auto-resolves
    assert confident_fuzzy_match(NAME_ROWS, "Kuldeep Kumr") == "Kuldeep Kumar"


def test_confident_match_declines_on_genuine_tie():
    # 'Siddhart' is an exact prefix of BOTH names -> tie -> not auto-resolved
    rows = NAME_ROWS + [{"Employee Name": "Siddharta Tiwari"}]
    assert confident_fuzzy_match(rows, "Siddhart") is None


def test_confident_match_resolves_clear_leader_over_distant_rival():
    # 'sidharth' clearly favours Siddharth Sharma over the more distant
    # Siddharta Tiwari -> auto-resolves to the clear leader
    rows = NAME_ROWS + [{"Employee Name": "Siddharta Tiwari"}]
    assert confident_fuzzy_match(rows, "Sidharth") == "Siddharth Sharma"


def test_confident_match_none_for_garbage():
    assert confident_fuzzy_match(NAME_ROWS, "zxcvbnm") is None


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"All {passed} tests passed.")
