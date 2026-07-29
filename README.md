# ACE MCP

An MCP server that exposes the `vwemployeerptclientforsanjaysir` reporting view
from the **Akhil_Reporting** SQL Server database.

## Project structure

```
ace_mcp/
├── server.py            # entry point (the path registered with Claude) — a thin shim
├── ace/                 # the package
│   ├── __init__.py
│   ├── config.py        # loads .env, builds the connection string
│   ├── database.py      # connection + cached snapshot + retries
│   ├── serialization.py # SQL types -> JSON-safe values
│   ├── aggregations.py  # pure helpers (filters, dates, open/aged) — unit-tested
│   ├── insights.py      # executive rollups (summary, team, clients, priority)
│   ├── tools.py         # the 14 @mcp.tool() functions
│   └── server.py        # FastMCP instance, pre-warm, main()
├── scripts/             # standalone dev utilities
│   ├── test_connection.py   # 3-step connectivity checker
│   └── find_view.py         # locate which DB holds a view
├── tests/
│   └── test_aggregations.py # offline unit tests (no DB)
├── docs/
│   └── VIEW_SCHEMA.md   # all 23 columns + question catalog
├── .env                 # secrets (git-ignored)
├── .env.example         # template to copy
├── requirements.txt
└── pyproject.toml
```

Layering: `config` → `database` → `aggregations`/`tools` → `server`. The pure
logic in `aggregations.py` has no DB dependency, so `tests/` run offline.

## Tools

**Primitives (compose almost any question):**

| Tool | What it does |
|------|--------------|
| `search_tickets(...)` | The workhorse. Filter by client, employee, task_type, priority, aspl_status, client_status, module, reported_by, completed_by, assigned_by, description `text`; plus `only_open`, `overdue`, and date windows (`date_field` + `last_days`/`since`/`until`); sort + limit. Returns `{matched, returned, unparsed_dates, rows}`. |
| `count_by(column, second_dimension=None, only_open=False, avg_ageing=False)` | Group & count; optional cross-tab and average ageing per group. |
| `get_ticket(aspl_number)` | Look up one ticket by ASPL# (`ASPL-165661` or `165661`). |

**Single-entity dashboards:**

| Tool | What it does |
|------|--------------|
| `get_employee_client_report(limit=100)` | Rows, newest first (`sno DESC`). `limit=0` for all. |
| `get_report_row_count()` | Total row count. |
| `overdue_or_aged(min_days=30, limit=100)` | Tasks ageing ≥ N days, oldest first. |
| `workload(employee_name)` | One employee: total/open + status & priority breakdown. |
| `client_summary(client_name)` | One client: total/open + task type, status & priority breakdown. |
| `search_description(text, limit=50)` | Substring search over Description. |
| `refresh_data()` | Force a fresh pull from the DB, bypassing the cache. |

**Executive / management insights (CEO / CTO / PM):**

| Tool | What it answers |
|------|-----------------|
| `executive_summary(aged_days=30)` | Portfolio health in one call: open vs closed, unassigned, immediate/high open, overdue & aged, bug share, top clients/employees by open load. |
| `team_performance(aged_days=30)` | Per-employee accountability & balance, ranked by open load: open, aged, overdue, oldest item, completion rate. "Who's overloaded / behind?" |
| `clients_needing_attention(aged_days=30, top=10)` | Clients ranked by escalation risk (immediate-open, aged/overdue, open). "Which clients need attention now?" |
| `priority_watch(aged_days=30)` | For Immediate & High: open, unassigned, aged + the worst-offending open tickets. "Are the important things being handled?" |

> **"Open"** = ASPL Status is not a done state (Delivered / Verified / Completed /
> Delivered On UAT / Cancel / Not Feasible). Everything else counts as open.
> **"Overdue"** = an open ticket whose Target Date is in the past.
> Date filters parse the view's `dd/MM/yyyy` text; rows whose date can't be
> parsed are reported in `unparsed_dates` rather than silently dropped.

### Caching (important)

The view is **slow to run** (~35s) and sits behind a flaky public-IP link, so the
server fetches the whole view **once** and caches it in memory. All tools answer
from that snapshot — instant after the first call. The snapshot auto-refreshes
after `DB_CACHE_TTL` seconds (default 120). Call `refresh_data()` to force it.

- First tool call in a session pays the ~35s fetch; the rest are instant.
- Transient link drops are retried automatically (3 attempts).
- Tune via `.env`: `DB_CACHE_TTL` (snapshot lifetime) and `DB_QUERY_TIMEOUT`.

## Setup

1. Install deps (already done on this machine):
   ```
   py -m pip install -r requirements.txt
   ```
2. Fill in `.env` (already configured):
   ```
   DB_HOST=43.242.214.195
   DB_PORT=1433
   DB_NAME=Akhil_Reporting
   DB_USER=...
   DB_PASSWORD=...
   DB_DRIVER=ODBC Driver 17 for SQL Server
   ```
3. Test the DB connection:
   ```
   py scripts/test_connection.py
   ```
4. Run the offline unit tests:
   ```
   py tests/test_aggregations.py
   ```

## Register with Claude Code

```
claude mcp add ace-mcp -- py C:\Users\HP\Desktop\akhil_system\ace_mcp\server.py
```

## Register with Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ace-mcp": {
      "command": "py",
      "args": ["C:\\Users\\HP\\Desktop\\akhil_system\\ace_mcp\\server.py"]
    }
  }
}
```

Then restart Claude Desktop. Ask it e.g. *"show the latest 10 rows from the
employee client report"*.

## Deploying as a remote MCP server (Cloud Run)

The server can run over Streamable HTTP instead of stdio, so it can be deployed
to Cloud Run and added as a remote connector in Claude Code / Claude Desktop /
other MCP clients, instead of only running on this machine.

This is gated by two env vars (unset = local stdio behavior, unchanged):

- `MCP_TRANSPORT=http` — switches transport from stdio to Streamable HTTP, listening on `0.0.0.0:$PORT`.
- `MCP_AUTH_TOKEN=<random secret>` — every HTTP request must send `Authorization: Bearer <token>`, or gets a 401. **Set this** — the Cloud Run service itself is deployed publicly reachable (`--allow-unauthenticated`), since most MCP client UIs can't do Google IAM auth. This token is the only thing standing between the public internet and your client/ticket data.

### 1. One-time GCP setup

```
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# Generate a strong connector token and store secrets in Secret Manager
# (PowerShell: use [Convert]::ToBase64String((1..32|%{Get-Random -Max 256})) or similar)
python -c "import secrets; print(secrets.token_urlsafe(32))"   # copy this value

echo -n "PASTE_THE_TOKEN_HERE" | gcloud secrets create ace-mcp-auth-token --data-file=-
echo -n "your_db_password"      | gcloud secrets create ace-mcp-db-password --data-file=-
```

### 2. Deploy

From the project root (this builds the `Dockerfile` via Cloud Build — no local Docker required):

```
gcloud run deploy ace-mcp \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars DB_HOST=43.242.214.195,DB_PORT=1433,DB_NAME=Akhil_Reporting,DB_USER=your_username,MCP_TRANSPORT=http \
  --set-secrets DB_PASSWORD=ace-mcp-db-password:latest,MCP_AUTH_TOKEN=ace-mcp-auth-token:latest
```

Note the HTTPS URL it prints (`https://ace-mcp-xxxxx.a.run.app`) — the MCP endpoint is `<that-url>/mcp`.

### 3. Add it as a connector

**Claude Code:**
```
claude mcp add --transport http ace-mcp https://ace-mcp-xxxxx.a.run.app/mcp \
  --header "Authorization: Bearer PASTE_THE_TOKEN_HERE"
```

**Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "ace-mcp": {
      "url": "https://ace-mcp-xxxxx.a.run.app/mcp",
      "headers": { "Authorization": "Bearer PASTE_THE_TOKEN_HERE" }
    }
  }
}
```

**claude.ai web custom connectors:** the web UI's "Add custom connector" flow is built around OAuth and may not expose a raw bearer-token/header field — check there before relying on it. Claude Code and Claude Desktop both support custom headers directly, so prefer those if the web UI doesn't cooperate.

### Redeploying after code changes

```
gcloud run deploy ace-mcp --source . --region us-central1
```
(omit `--set-env-vars`/`--set-secrets` once set — Cloud Run keeps the previous revision's config unless you override it)

## Security

- `.env` holds DB credentials in plaintext and is git-ignored — do not commit it.
- The database is on a public IP; keep credentials private.
- The Cloud Run deployment is publicly reachable network-wise; `MCP_AUTH_TOKEN` is the actual access control. Treat it like a password — rotate it via `gcloud secrets versions add ace-mcp-auth-token --data-file=-` followed by a redeploy if it ever leaks.
