# SecPipe — Plataforma de Segurança para CI/CD (MVP)

Pipeline de segurança que roda **SAST + SCA + Secrets + IaC** em cada push/PR,
normaliza os resultados em um formato único e aplica um **gate de política**
(bloqueia o merge se houver achados acima do limite configurado).

## Motores utilizados

| Capacidade | Ferramenta | Como roda |
|---|---|---|
| SAST | [Semgrep](https://semgrep.dev) | container `semgrep/semgrep` |
| SCA + IaC + Secrets (fs) | [Trivy](https://trivy.dev) | `aquasecurity/trivy-action` |
| Secrets (histórico git) | [Gitleaks](https://github.com/gitleaks/gitleaks) | container `zricethezav/gitleaks` |

Todos exportam **SARIF**, que é o formato unificado da plataforma.

## Estrutura

```
.
├── .github/workflows/
│   ├── security-scan.yml     # workflow reutilizável (workflow_call) — o "produto"
│   └── example-caller.yml    # exemplo de como um repo consome o workflow
├── configs/
│   ├── gitleaks.toml         # regras extras / allowlist de falsos positivos
│   └── trivy.yaml            # config do Trivy (scanners, severidades)
├── policy/
│   └── policy.yml            # limites do gate (o que bloqueia o merge)
├── scripts/
│   ├── normalize.py          # SARIF(s) -> findings.json unificado + dedup
│   ├── gate.py               # aplica policy.yml sobre findings.json (exit 1 = bloqueia)
│   └── upload.py             # envia findings.json para o dashboard (stdlib apenas)
├── dashboard/
│   ├── app.py                # backend FastAPI + SQLite (ingest, triagem, histórico)
│   ├── static/index.html     # interface web: cards, tendência, tabela de triagem
│   └── requirements.txt
└── run-local.ps1             # roda os 3 scanners localmente via Docker
```

## Dashboard (interface web)

```powershell
cd dashboard
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Abra http://localhost:8000. Recursos:

- **Cards** de findings abertos por severidade, corrigidos e nº de repos.
- **Tendência**: gráfico da evolução por scan (critical/high/medium/low).
- **Triagem**: tabela filtrável (repo/severidade/status); mudar o status para
  `false_positive` ou `accepted` persiste entre scans; finding que some do scan
  vira `fixed` automaticamente e reabre se voltar.

Para o pipeline enviar resultados automaticamente, configure no repositório
(ou na organização) do GitHub:

- **Variable** `SECPIPE_DASHBOARD_URL` — URL pública do dashboard.
- **Secret** `SECPIPE_TOKEN` — defina o mesmo valor na env do servidor
  (`SECPIPE_TOKEN=...` antes do `uvicorn`) para exigir autenticação no ingest.

Sem a variable configurada, o step de upload é pulado e tudo continua
funcionando só com a aba Security + artifacts.

## Uso em outro repositório

Crie `.github/workflows/security.yml` no repo alvo:

```yaml
name: security
on: [pull_request]
jobs:
  scan:
    uses: SUA_ORG/ci_cd/.github/workflows/security-scan.yml@main
```

## Uso local (requer Docker)

```powershell
./run-local.ps1 -Target C:\caminho\do\repo
```

Os resultados ficam em `./out/findings.json` e o gate imprime o veredito.

## Roadmap

- [x] Fase 1: SAST + SCA + Secrets + IaC com gate de merge
- [x] Fase 2: dashboard próprio (FastAPI + SQLite), triagem, histórico multi-repo
- [ ] Fase 3: DAST (OWASP ZAP contra staging) e runtime (Falco/YARA)
