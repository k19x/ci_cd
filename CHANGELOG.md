# Changelog

All notable changes to SecPipe are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.0] - 2026-08-29

### Added
- **Projects tab — Public/Private filter**: tabs All / Public / Private filtram a lista de projetos por visibilidade
- **Projects tab — Language chips**: cada projeto exibe as linguagens detectadas (Python, Go, JavaScript, etc.) buscadas da API do GitHub
- **Visibility badge**: badge 🌐 Public / 🔒 Private ao lado do nome do projeto
- **Botão ↻ Refresh meta**: atualiza visibilidade e linguagens de um projeto individual via GitHub API
- **Auto-fetch metadata**: ao adicionar um projeto, os metadados são buscados automaticamente em background
- **Backend `/api/repos/{name}/refresh-meta`**: novo endpoint que consulta GitHub e persiste `visibility` e `languages` no SQLite

### Changed
- Coluna "Languages" adicionada à tabela de Projects

## [0.5.6] - 2026-08-29

### Added
- Stat strip clicável: cada card (Total, Critical, High, Medium, Low, Corrigidos) navega para Results com filtro de severidade/status pré-aplicado
- Tabela "Último scan por projeto" clicável: linha abre Results filtrado por repo; chips Critical/High filtram por repo + severidade
- Hover nos stat-cells com underline azul para indicar interatividade

## [0.5.5] - 2026-08-29

### Changed
- Aba "Vulnerabilidades" renomeada para "Findings"

## [0.5.4] - 2026-08-29

### Added
- Paginação na aba Results: 25/50/100 resultados por página, controles Anterior/Próxima, indicador "X–Y de N · Pág. P/T"
- Triage não reseta a página — permanece na mesma posição ao marcar um finding

## [0.5.3] - 2026-08-29

### Changed
- Sidebar redesenhada: background `#111318` (charcoal neutro, sem tint navy), hover `#1c1f27`
- Accent color trocado de laranja `#E8612D` → azul índigo `#4F7EF7` (botões, borda ativa, brand icon, links)
- HIGH severity agora usa âmbar `#E07339` independente do accent, evitando confusão visual

## [0.5.2] - 2026-08-29

### Added
- Botão **▶ Scan** na aba Projects: dispara `workflow_dispatch` no `security.yml` de cada repo diretamente pelo dashboard, sem precisar abrir o GitHub
- Endpoint `POST /api/repos/{repo}/dispatch` — chama GitHub Actions API com fallback automático `main` → `master`

## [0.5.1] - 2026-08-29

### Fixed
- Aplicado `fix-workflows.ps1` em todos os 44 repos `k19x/*`: `security.yml` agora contém `permissions:` no nível do workflow e `workflow_dispatch:` trigger em 100% dos repositórios

## [0.5.0] - 2026-08-29

### Added
- `Dockerfile` — imagem Python 3.12-slim com FastAPI + git; monta o repo inteiro em `/workspace` para acesso ao policy e git push
- `docker-compose.yml` — dois serviços: `dashboard` (FastAPI) + `cloudflared` (tunnel); suporta quick tunnel (sem conta) e named tunnel (URL permanente com domínio)
- `.env.example` — template de variáveis de ambiente (GITHUB_TOKEN, SECPIPE_TOKEN, CLOUDFLARE_TUNNEL_TOKEN)
- Banco SQLite persistido em volume Docker `secpipe-data:/data`
- Env vars `SECPIPE_DB`, `SECPIPE_REPO_ROOT`, `SECPIPE_POLICY` para configurar paths sem rebuild

### Changed
- `dashboard/app.py`: `DB_PATH`, `REPO_ROOT` e `POLICY_PATH` agora respeitam variáveis de ambiente (`SECPIPE_DB`, `SECPIPE_REPO_ROOT`, `SECPIPE_POLICY`)

## [0.4.0] - 2026-08-29

### Added
- Aba **Vulnerabilidades**: histórico de todos os scans com repo, branch, commit e chips coloridos `C·H·M·L` por severidade; filtrável por projeto
- Aba **Configurações**: editor de política de bloqueio (limites por severidade + gerenciamento do allowlist) com botões "Salvar policy.yml" e "Commit & Push → GitHub"
- Endpoint `GET /api/scans` — retorna histórico completo de scans com contagens por severidade
- Endpoints `GET /api/policy`, `PUT /api/policy`, `POST /api/policy/push` — leitura, escrita e push da política via dashboard

## [0.3.1] - 2026-08-29

### Added
- Dashboard auto-refresh a cada 30 s: stats, trend e last scans atualizam automaticamente sem interação do usuário
- Indicador de horário da última atualização no topbar ("Auto-refresh 30s · HH:MM:SS") que aparece apenas na aba Dashboard

## [0.3.0] - 2026-08-29

### Added
- `fix-workflows.ps1` — script para atualizar `security.yml` em todos os repos da conta com `permissions:` + `workflow_dispatch:` corretos
- Tema dark "escuro Chrome" no dashboard: paleta `#202124`/`#2d2e30`/`#3c4043` em dark mode

### Fixed
- `startup_failure` em todos os repos causado por ausência de `permissions:` no workflow caller; corrigido adicionando bloco `permissions: { contents: read, security-events: write, actions: read }` no nível do workflow
- Falso positivo do gate: regra `yaml.github-actions.security.secrets-inherit.secrets-inherit` adicionada ao allowlist em `policy/policy.yml` (`secrets: inherit` é seguro para o workflow `k19x/ci_cd` próprio)
- `.github/workflows/security.yml` excluído do escopo do scan de SAST

## [0.2.0] - 2026-08-29

### Added
- Dashboard UI completamente redesenhado com layout Checkmarx One: sidebar escura com navegação, stat strip de severidades, tabela de últimos scans, gráfico de tendências por repo
- Integração com GitHub Actions API na aba Scans com auto-refresh a cada 15 s
- Aba Projects com cadastro de repos, snippet de workflow, contagem de findings e link para remoção
- Suporte a triage inline na tabela de Results (To Review / Not Exploitable / Risk Accepted / Fixed)
- Filtros de repo, severidade, status e engine na aba Results
- Badge de critical no topbar quando há findings críticos abertos

### Fixed
- `startup_failure` inicial: repo `k19x/ci_cd` tornado público para permitir acesso ao reusable workflow
- Rota de triage PATCH `/api/findings/{repo:path}/{fid}` com suporte a nomes `org/repo` (barras) via `{repo:path}` no FastAPI

## [0.1.0] - 2026-08-28

### Added
- Plataforma SecPipe MVP: SAST (Semgrep), SCA + IaC + Secrets (Trivy), Secrets histórico (Gitleaks)
- Workflow reutilizável `security-scan.yml` no `k19x/ci_cd` com 4 jobs: semgrep, trivy, gitleaks, gate
- Normalização SARIF → `findings.json` via `scripts/normalize.py` com dedup por fingerprint
- Gate de política `scripts/gate.py` configurável por severidade via `policy/policy.yml`
- Upload de findings para o dashboard via `scripts/upload.py` (stdlib pura)
- Dashboard FastAPI + SQLite em `dashboard/app.py` com endpoints: `/api/ingest`, `/api/overview`, `/api/findings`, `/api/repos`, `/api/runs`, `/api/trend`
- Script `integrate-all.ps1` para integrar todos os repos da conta k19x em batch
- 44 repositórios integrados com `security.yml` via API do GitHub
