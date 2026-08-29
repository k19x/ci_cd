FROM python:3.12-slim AS base

# ── dependências de sistema ────────────────────────────────────────────────
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && apt-get clean

# ── usuário não-root ───────────────────────────────────────────────────────
RUN groupadd -r -g 1000 secpipe \
 && useradd  -r -u 1000 -g secpipe -d /app -s /sbin/nologin secpipe

WORKDIR /app

# ── dependências Python (camada cacheável) ─────────────────────────────────
COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# ── código da aplicação ────────────────────────────────────────────────────
COPY dashboard/ .

# ── diretórios necessários com permissão correta ───────────────────────────
RUN mkdir -p /app/policy /data \
 && chown -R secpipe:secpipe /app /data

# ── variáveis de ambiente com defaults seguros ─────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SECPIPE_DB=/data/secpipe.db \
    SECPIPE_POLICY=/app/policy/policy.yml

USER secpipe

EXPOSE 8200

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8200/api/overview', timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200", \
     "--no-access-log", "--workers", "1"]
