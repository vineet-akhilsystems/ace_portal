# Troubleshooting: Cloud Run deploy worked, but tools couldn't read the database

**Date:** 2026-07-29
**Service:** `ace-mcp` on GCP Cloud Run (project `akhil-mcp-server`, region `us-central1`)
**Symptom:** The server was up and every tool was listed, but any tool that
actually reads data failed. There were **two separate bugs** hiding behind one
symptom — we only found the second one *after* fixing the first.

---

## How we diagnosed it

The key idea: **test each layer separately, from the outside in.** Don't guess —
prove which layer is broken.

| Layer | Test we ran | Result |
|-------|-------------|--------|
| 1. Is the server reachable? | `curl` the `/mcp` URL with no auth | `HTTP 401 Unauthorized` → server is **up** and auth works |
| 2. Does auth + MCP protocol work? | `curl` with `Authorization: Bearer <token>`, call `initialize` then `tools/list` | `200 OK`, 16 tools listed → **protocol layer fine** |
| 3. Does the database work? | Call a real tool (`get_report_row_count`) | ❌ **Error** → the problem is the DB layer |

Lesson: a service that "starts fine" can still be broken deeper down. A health
check that only pings the URL would have shown green while the DB was dead.
**Always test the layer that does the real work** (here, an actual DB query).

---

## Bug #1 — TLS handshake failure (old SQL Server + modern OpenSSL)

### The error
```
[Microsoft][ODBC Driver 17 for SQL Server]SSL Provider:
[error:0A000102:SSL routines::unsupported protocol] (SQLDriverConnect)
```

### What it means
`unsupported protocol` = the client and server could not agree on a **TLS
version**. The TCP connection reached SQL Server, but the encrypted handshake
that happens *before* login was rejected.

### Why it happened
- The SQL Server is old and only speaks **TLS 1.0 / 1.1**.
- Our container image `python:3.12-slim` had silently floated to a newer Debian
  release shipping **OpenSSL 3**, which **refuses anything below TLS 1.2** by
  default (security level 2).
- So: old server offers TLS 1.0 → modern client says "no" → handshake fails.

### Why it worked on your laptop but not in the cloud
On Windows, the ODBC driver uses **SChannel** (Windows' own TLS stack), which
still allows the older protocol. On Cloud Run's Linux + OpenSSL 3, it doesn't.
**"Works on my machine" often means the two machines have different TLS policies.**

### The fix
Two changes in the `Dockerfile`:

1. **Pinned the base image** so it stops drifting between Debian releases:
   ```dockerfile
   FROM python:3.12-slim-bookworm   # was: python:3.12-slim
   ```
   (Bookworm = Debian 12, which also matches the Microsoft ODBC repo the file
   already targets: `config/debian/12/prod.list`.)

2. **Relaxed OpenSSL's policy** with a self-contained config file, pointed to via
   the `OPENSSL_CONF` environment variable:
   ```dockerfile
   RUN set -eux; printf '%s\n' \
       'openssl_conf = openssl_init' \
       '' \
       '[openssl_init]' \
       'ssl_conf = ssl_sect' \
       '' \
       '[ssl_sect]' \
       'system_default = system_default_sect' \
       '' \
       '[system_default_sect]' \
       'CipherString = DEFAULT@SECLEVEL=0' \
       'MinProtocol = TLSv1' \
       > /etc/ssl/relaxed-openssl.cnf
   ENV OPENSSL_CONF=/etc/ssl/relaxed-openssl.cnf
   ```
   - `MinProtocol = TLSv1` → allow negotiating down to TLS 1.0.
   - `SECLEVEL=0` → allow the older ciphers that go with it.

> ⚠️ A false start worth learning from: we first tried to `sed`-edit the existing
> `/etc/ssl/openssl.cnf`, assuming it contained a `[system_default_sect]` block.
> It doesn't — Debian's default file has no such section. Editing a line that
> isn't there silently does nothing. **Shipping our own complete config file was
> more reliable than patching an existing one whose contents we assumed.**
> (We added a build-time `grep` check that *failed the build* when the edit
> didn't apply — that's how we caught the wrong assumption fast.)

### Proper long-term fix
Relaxing client TLS is a pragmatic workaround. The real fix is to **enable TLS
1.2 on the SQL Server itself** so no client has to weaken its security.

---

## Bug #2 — Database password was never set on the service

### The error (appeared *after* the TLS fix)
```
[Microsoft][ODBC Driver 17 for SQL Server][SQL Server]
Login failed for user 'satender.datt'. (18456)
```

### What it means
This is a **login/authentication** failure — a totally different layer from Bug
#1. Getting here was actually *good news*: it proved TLS now worked, because we
reached the point where SQL Server checks credentials.

### Why it happened
The Cloud Run service's environment variables were:
`DB_HOST, DB_PORT, DB_NAME, DB_USER, MCP_TRANSPORT, MCP_AUTH_TOKEN` — **no
`DB_PASSWORD` at all.** It existed in the local `.env`, but was never wired into
the deployed service, so the app tried to log in with an empty password.

### The fix
A Secret Manager secret `ace-mcp-db-password` already existed with the correct
value (we verified it matched the local `.env` — by comparing lengths, never
printing the secret). It just wasn't connected. One command wired it in:
```bash
gcloud run services update ace-mcp \
  --project akhil-mcp-server --region us-central1 \
  --update-secrets DB_PASSWORD=ace-mcp-db-password:latest
```

---

## The lessons (the part to actually remember)

1. **Test layer by layer, outside-in.** Reachable → authenticated → protocol →
   real query. Each test isolates one layer so you know exactly where it breaks.
2. **One symptom can hide multiple bugs.** Fixing TLS just revealed the missing
   password. Keep going until the real end-to-end action succeeds.
3. **Read the error's *category*.** `SSL routines` = transport/TLS.
   `Login failed (18456)` = authentication. The wording tells you the layer.
4. **Pin your base images.** `python:3.12-slim` is a moving target; `-bookworm`
   is stable. Unpinned images cause "it worked last month" mysteries.
5. **"Works locally" ≠ "works in the cloud."** Different OS = different TLS
   stack, different defaults. The environment is part of the bug.
6. **Don't patch what you didn't verify exists.** We assumed a config section
   was there; it wasn't. Prefer shipping known-good config, and add a check that
   fails loudly when an assumption is wrong.
7. **Config/secrets live in two places** (local `.env` *and* the cloud service).
   They drift. A value being in `.env` does **not** mean it's on the server.

---

## Quick reference — how to re-test the deployed server

```bash
URL="https://ace-mcp-164261714722.us-central1.run.app/mcp"
TOKEN=$(gcloud secrets versions access latest --secret=ace-mcp-auth-token --project akhil-mcp-server)

# 1. initialize (grab the session id from the response headers)
# 2. send notifications/initialized with that session id
# 3. call a real tool, e.g. get_report_row_count  -> should return a number, isError:false
```
A successful `get_report_row_count` returning a count (e.g. `503`) means all
layers — network, auth, MCP protocol, TLS, DB login, and query — are healthy.

---

# Deploying the same container to AWS

**Good news first:** the *hard* fix (the TLS relaxation + pinned base image) is
baked into the Docker image, so it travels to AWS automatically. You do **not**
have to solve the `unsupported protocol` error again.

What you *do* have to redo on AWS is everything that lived in the **Cloud Run
service config, not the image**: the environment variables, the two secrets, and
network reachability to the SQL Server. This section is a checklist for that.

The closest AWS equivalent to Cloud Run is **AWS App Runner** (deploy a
container, set env + secrets, get an HTTPS URL). Steps below use it. ECS Fargate
works too and gives more control, but App Runner is the simplest match.

> Note: App Runner can only *build from source* using its managed language
> runtimes — it can't build our `Dockerfile` (which installs the MS ODBC driver
> via `apt`). So we push a **prebuilt image to ECR** and point App Runner at it.

## What does NOT come from the image (you must set these)

| Key | Type | Value |
|-----|------|-------|
| `DB_HOST` | env var | your SQL Server IP/host |
| `DB_PORT` | env var | `1433` |
| `DB_NAME` | env var | the database name |
| `DB_USER` | env var | `satender.datt` |
| `DB_PASSWORD` | **secret** | the DB password |
| `MCP_AUTH_TOKEN` | **secret** | the bearer token clients must send |
| `MCP_TRANSPORT` | already in the Dockerfile (`http`) | nothing to do |
| `PORT` | App Runner provides it; server defaults to `8080` | nothing to do |

## Step 1 — Build and push the image to ECR

Requires Docker running locally + AWS CLI configured. Replace `<ACCOUNT>` and
`<REGION>` (e.g. `us-east-1`).

```bash
ACCOUNT=<ACCOUNT>; REGION=<REGION>; REPO=ace-mcp
aws ecr create-repository --repository-name $REPO --region $REGION

# authenticate docker to ECR
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# build for the platform App Runner runs (linux/amd64) and push
docker build --platform linux/amd64 -t $REPO .
docker tag $REPO:latest $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
```

> `--platform linux/amd64` matters if you build on an ARM Mac — App Runner runs
> x86 and would reject an arm64 image.

## Step 2 — Create the two secrets

```bash
aws secretsmanager create-secret --name ace-mcp-db-password \
  --secret-string 'THE_DB_PASSWORD' --region $REGION
aws secretsmanager create-secret --name ace-mcp-auth-token \
  --secret-string 'THE_BEARER_TOKEN' --region $REGION
```
(Or paste the values in the console. Never commit them.)

## Step 3 — Create the App Runner service

Easiest in the **console**: App Runner → Create service → *Container registry*
→ pick the ECR image → then configure:

- **Port:** `8080`
- **Environment variables:** `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`
  (plain values).
- **Environment secrets:** `DB_PASSWORD` → the `ace-mcp-db-password` secret ARN;
  `MCP_AUTH_TOKEN` → the `ace-mcp-auth-token` secret ARN.
- **Instance role (IAM):** must allow `secretsmanager:GetSecretValue` on those
  two secrets — otherwise the container starts with empty secrets and you get the
  `Login failed (18456)` error again (exactly Bug #2, just on AWS).
- **Access role:** App Runner needs permission to pull from ECR (the console
  offers to create this automatically).

### ⚠️ AWS-specific gotcha: the health check

App Runner's default health check is **TCP** (just checks the port is open) —
leave it that way. **Do not switch it to an HTTP health check on `/`.** Our
server wraps *every* path in the bearer-auth middleware, so `/` returns
`401 Unauthorized`. An HTTP health check expects `200`, would see the `401`,
decide the service is unhealthy, and kill it in a restart loop. TCP avoids this.

## Step 4 — ⚠️ Network reachability (the thing most likely to bite you)

Cloud Run reached your SQL Server from Google's IP range. App Runner connects
from a **different, AWS egress IP**. If your SQL Server has a firewall / IP
allowlist, it will **reject AWS until you add AWS's IP**.

- By default App Runner egresses from AWS-managed IPs that are **not stable** —
  bad for an allowlist.
- For a **stable egress IP** to hand to the DBA: attach a **VPC connector**, put
  it on a private subnet whose route table sends `0.0.0.0/0` through a **NAT
  gateway with an Elastic IP**. That EIP is then the single IP to allowlist on
  the SQL Server.
- If the SQL Server allows any IP, you can skip this.

## Step 5 — Test it (same method as Cloud Run, new URL)

App Runner gives you a URL like `https://xxxx.<region>.awsapprunner.com`. Test
the same way — reachable → auth → `initialize` → `tools/list` → call
`get_report_row_count`. A returned count means all layers are healthy on AWS too.

## AWS deployment checklist (tick these tomorrow)

- [ ] Image built `--platform linux/amd64` and pushed to ECR
- [ ] `ace-mcp-db-password` + `ace-mcp-auth-token` secrets created
- [ ] App Runner service: port `8080`, 4 env vars set, 2 secrets wired
- [ ] Instance IAM role can read both secrets
- [ ] Health check left as **TCP** (not HTTP `/`)
- [ ] SQL Server firewall allows the AWS egress IP (NAT gateway EIP if needed)
- [ ] `get_report_row_count` returns a number over the new AWS URL
