# Pinned to bookworm (Debian 12): the MS ODBC repo below targets debian/12, and
# bookworm's openssl.cnf carries the [system_default_sect]/CipherString policy
# the TLS relaxation step edits. The unpinned tag floated to trixie (Debian 13),
# which broke both assumptions.
FROM python:3.12-slim-bookworm

# --- Microsoft ODBC Driver 17 for SQL Server (required by pyodbc) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 unixodbc unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Old SQL Server only supports TLS 1.0/1.1; OpenSSL 3 (Debian 12) blocks it by
# default (SECLEVEL=2, min TLS 1.2), which makes the ODBC pre-login TLS handshake
# fail with "SSL routines::unsupported protocol". Debian's stock openssl.cnf has
# no [system_default_sect] to edit, so ship a self-contained relaxed config and
# point OpenSSL at it via OPENSSL_CONF. SECLEVEL=0 + MinProtocol TLSv1 lets the
# handshake negotiate down to what the legacy server offers.
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
    > /etc/ssl/relaxed-openssl.cnf; \
    cat /etc/ssl/relaxed-openssl.cnf
ENV OPENSSL_CONF=/etc/ssl/relaxed-openssl.cnf

ENV MCP_TRANSPORT=http
ENV PYTHONUNBUFFERED=1

# Cloud Run injects PORT and expects the container to listen on it.
CMD ["python", "server.py"]
