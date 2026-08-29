FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/ .

RUN mkdir -p /app/policy /data

ENV SECPIPE_DB=/data/secpipe.db
ENV SECPIPE_POLICY=/workspace/policy/policy.yml
ENV SECPIPE_REPO_ROOT=/workspace

EXPOSE 8200

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
