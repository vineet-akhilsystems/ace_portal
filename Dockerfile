FROM python:3.12-slim

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

ENV MCP_TRANSPORT=http
ENV PYTHONUNBUFFERED=1

# Cloud Run injects PORT and expects the container to listen on it.
CMD ["python", "server.py"]
