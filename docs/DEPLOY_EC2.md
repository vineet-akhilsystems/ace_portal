# Deploying ace-mcp to an AWS EC2 instance

A step-by-step runbook for running the ace-mcp server on a plain EC2 instance
(Amazon Linux 2023), pulling the code with `git clone`, and exposing it over
HTTPS **without owning a domain**.

**What you end up with**

```
Claude (claude.ai / Claude Code)
        │  HTTPS  https://<ip-with-dashes>.sslip.io/mcp
        ▼
EC2 instance ──► Caddy (ports 80/443, auto Let's Encrypt cert)
                    │  http://127.0.0.1:8080
                    ▼
                 ace-mcp container (Docker) ──► SQL Server (TLS 1.0, port 1433)
```

The examples use the current instance, IP `16.4.24.168`. If your IP is
different, use your own everywhere you see it.

---

## Step 0 — Prerequisites (AWS console)

1. **An EC2 instance**: Amazon Linux 2023, x86_64. A `t3.micro`/`t2.micro`
   (1 GB RAM) works if you add swap (Step 2), but `t3.small` is more comfortable.
   Give it at least 8 GB of disk.
2. **The instance's key pair** `.pem` file (e.g. `ace_portal.pem`) saved on your
   PC.
3. **A security group** with these inbound rules:

   | Port | Source | Why |
   |------|--------|-----|
   | 22 (SSH) | **My IP** only | for you to log in |
   | 80 (HTTP) | 0.0.0.0/0 | Let's Encrypt certificate check + redirect to HTTPS |
   | 443 (HTTPS) | 0.0.0.0/0 | the MCP endpoint |

   Do **not** open 8080. The app listens only on localhost, behind Caddy.
4. **(Strongly recommended) An Elastic IP** attached to the instance.
   Otherwise the public IP, and with it your URL, changes whenever the
   instance is stopped and started. An Elastic IP is free while it's
   attached to a running instance.
5. **The SQL Server firewall allowlist.** If the DB only accepts known IPs, ask
   whoever manages it to allow the instance's public IP on port 1433.

---

## Step 1 — SSH into the instance (from Windows)

Windows' SSH refuses a key file that other users can read
(`UNPROTECTED PRIVATE KEY FILE` / `bad permissions`). Lock it down once in
PowerShell:

```powershell
cd C:\Users\HP\Documents
icacls .\ace_portal.pem /inheritance:r
icacls .\ace_portal.pem /remove "ASPL262\CodexSandboxUsers" "Users" "Authenticated Users" "Everyone"
icacls .\ace_portal.pem /grant:r "$($env:USERNAME):(R)"
icacls .\ace_portal.pem          # should list ONLY your user
```

Then connect:

```powershell
ssh -i "ace_portal.pem" ec2-user@ec2-16-4-24-168.ap-south-1.compute.amazonaws.com
```

All remaining steps run **on the instance** (in this SSH session).

---

## Step 2 — Install Docker, git, and swap

```bash
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user      # lets you run docker without sudo (after re-login)

# 1 GB swap so the Docker build doesn't run out of memory on a 1 GB instance
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

Log out and back in (`exit`, then SSH again) so the `docker` group applies.
Check with: `docker --version && git --version && swapon --show`.

---

## Step 3 — Give the instance read access to the GitHub repo

The repo `vineet-akhilsystems/ace_portal` is private, so the instance
needs its own credentials. Use a **deploy key**: an SSH key that can read this
one repo and nothing else. Don't put your personal GitHub password or token on
the server.

**3a. Create the key on the instance:**

```bash
ssh-keygen -t ed25519 -C "ace-mcp-ec2" -f ~/.ssh/ace_deploy -N ""
cat ~/.ssh/ace_deploy.pub          # copy this whole line
```

**3b. Add it on GitHub** (you need admin rights on the repo, or ask an admin):

1. Open https://github.com/vineet-akhilsystems/ace_portal
2. **Settings → Deploy keys → Add deploy key**
3. Title: `ace-mcp-ec2`. Key: paste the line from 3a.
4. Leave **"Allow write access" unchecked**. The server only needs to read.
5. **Add key**.

**3c. Tell SSH to use that key for GitHub:**

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/ace_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

ssh -T git@github.com
# Expected: "Hi vineet-akhilsystems/ace_portal! You've successfully authenticated..."
# (answer "yes" to the host fingerprint prompt the first time)
```

---

## Step 4 — Clone the code

```bash
# If /opt/ace-mcp already exists from an earlier manual upload, move it aside first:
[ -d /opt/ace-mcp ] && sudo mv /opt/ace-mcp /opt/ace-mcp.old-$(date +%F)

sudo mkdir -p /opt/ace-mcp
sudo chown ec2-user:ec2-user /opt/ace-mcp
git clone git@github.com:vineet-akhilsystems/ace_portal.git /opt/ace-mcp
cd /opt/ace-mcp && git log --oneline -3
```

The repo's git-ignore rules keep the local credentials file out of git, so
**no secrets come with the clone**. You add them in the next step.

---

## Step 5 — Add the secrets (on the instance only)

Create the settings file outside the code folder, readable only by root:

```bash
sudo mkdir -p /etc/ace-mcp
sudo nano /etc/ace-mcp/secrets
```

Paste and fill in (same keys as `.env.example`):

```
DB_HOST=<sql server ip>
DB_PORT=1433
DB_NAME=Akhil_Reporting
DB_USER=<db user>
DB_PASSWORD=<db password>
DB_DRIVER=ODBC Driver 17 for SQL Server
MCP_AUTH_TOKEN=<long random token>
```

- Generate a token with `openssl rand -hex 32`, or reuse the existing Cloud
  Run token so clients keep working.
- Don't use quotes around values, and don't put spaces around `=`.

Save (`Ctrl+O`, `Enter`, `Ctrl+X`), then lock it down:

```bash
sudo chown root:root /etc/ace-mcp/secrets
sudo chmod 600 /etc/ace-mcp/secrets
```

> Never copy your laptop's `.env` to the server or commit it. Type or paste
> the values here directly.

---

## Step 6 — Build and run the container

```bash
cd /opt/ace-mcp
docker build -t ace-mcp .
```

The first build takes a few minutes. It installs the Microsoft ODBC driver and
applies the TLS 1.0 relaxation needed by the old SQL Server (see
`TROUBLESHOOTING_CLOUD_RUN_DB.md`).

```bash
docker run -d --name ace-mcp \
  --restart unless-stopped \
  --env-file /etc/ace-mcp/secrets \
  -p 127.0.0.1:8080:8080 \
  ace-mcp

docker logs -f ace-mcp     # Ctrl+C to stop following
```

- **`127.0.0.1:8080`** means the app can only be reached from the instance
  itself. The internet reaches it only through Caddy (HTTPS).
- **`--restart unless-stopped`** brings it back after crashes and reboots.

Quick local check (should print `401`, because no token was sent):

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/mcp
```

---

## Step 7 — HTTPS with Caddy (no domain needed)

[sslip.io](https://sslip.io) is a free DNS service. Any hostname like
`16-4-24-168.sslip.io` resolves to `16.4.24.168`, so Let's Encrypt can issue a
real certificate for it. Write your IP with dashes:

```bash
HOSTNAME=16-4-24-168.sslip.io      # <-- change if your IP is different

sudo mkdir -p /etc/caddy
echo "$HOSTNAME {
    reverse_proxy 127.0.0.1:8080
}" | sudo tee /etc/caddy/Caddyfile

docker run -d --name caddy \
  --restart unless-stopped \
  --network host \
  -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v caddy_data:/data -v caddy_config:/config \
  caddy:2

docker logs -f caddy      # wait for "certificate obtained successfully"
```

If the certificate fails, ports 80/443 aren't open in the security group
(Step 0).

> If you later get a real domain, point its A record at the IP, replace the
> hostname in `/etc/caddy/Caddyfile`, and run `docker restart caddy`.

---

## Step 8 — Verify end to end

From your PC or the instance. Replace `<TOKEN>` with your `MCP_AUTH_TOKEN`:

```bash
URL=https://16-4-24-168.sslip.io/mcp
TOKEN=<TOKEN>

# 1. No token -> must be 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST $URL

# 2. initialize -> grab the session id from the response headers
curl -si -X POST "$URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | grep -i mcp-session-id
SID=<paste the mcp-session-id value>

# 3. tell the server initialization is done
curl -s -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H "Mcp-Session-Id: $SID" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 4. a real query -> should return a row count with "isError":false
curl -s -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H "Mcp-Session-Id: $SID" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_report_row_count","arguments":{}}}'
```

A row count from step 4 means every layer works: HTTPS, token, MCP, TLS to SQL
Server, DB login, and the query. The first call may take ~35 s while the server
loads the view snapshot.

---

## Step 9 — Connect Claude to it

- **claude.ai → Settings → Connectors → Add custom connector**
  URL: `https://16-4-24-168.sslip.io/mcp?key=<TOKEN>`
- **Claude Code:**
  ```bash
  claude mcp add --transport http ace https://16-4-24-168.sslip.io/mcp \
    --header "Authorization: Bearer <TOKEN>"
  ```

Treat a URL that contains `?key=` like a password. Don't paste it in shared
chats.

---

## Updating to new code

Push your changes to GitHub from your PC, then on the instance:

```bash
cd /opt/ace-mcp
git pull
docker build -t ace-mcp .
docker rm -f ace-mcp
docker run -d --name ace-mcp --restart unless-stopped \
  --env-file /etc/ace-mcp/secrets -p 127.0.0.1:8080:8080 ace-mcp
docker image prune -f        # free disk from old images (8 GB fills up)
```

Changing a secret uses the same flow: edit `/etc/ace-mcp/secrets`, then run
the `docker rm -f` + `docker run` lines. A plain restart doesn't re-read the
env file.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `bad permissions` / `UNPROTECTED PRIVATE KEY FILE` on SSH | `.pem` readable by other Windows users | Step 1 `icacls` commands |
| `Permission denied (publickey)` | wrong key pair or wrong user | use the `.pem` chosen at launch; user is `ec2-user` on Amazon Linux |
| `git clone` → `Permission denied (publickey)` | deploy key not added / not used | Step 3b; run `ssh -T git@github.com` to test |
| Caddy can't get a certificate | ports 80/443 closed | open them in the security group |
| URL stopped working after stop/start | public IP changed | attach an Elastic IP; update the hostname in the Caddyfile |
| Tool call → `SSL routines::unsupported protocol` | image built without the TLS fix | rebuild from the repo `Dockerfile` (don't change the base image) |
| Tool call → `Login failed for user (18456)` | wrong/missing `DB_PASSWORD` | fix `/etc/ace-mcp/secrets`, then `docker rm -f` + `docker run` |
| Tool call → timeout / `TCP Provider` error | SQL Server firewall blocks the EC2 IP | add the instance IP to the DB allowlist |
| Every request → `401` | wrong token, or `MCP_AUTH_TOKEN` missing | compare with `/etc/ace-mcp/secrets` |
| Build killed / very slow | out of memory | add swap (Step 2) or use a larger instance |

Useful commands: `docker ps`, `docker logs --tail 100 ace-mcp`,
`docker logs --tail 100 caddy`, `df -h /`, `free -m`.
