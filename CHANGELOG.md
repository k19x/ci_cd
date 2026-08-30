# Changelog

All notable changes to SecPipe are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] - 2026-08-30

### Added — paridade de funções estilo Checkmarx em todas as abas
- **Scans**: busca por projeto/branch, filtro por status (success/failure/running/queued/...), exportação CSV
- **Results**: busca livre (regra, arquivo, mensagem, projeto), **triagem em massa** — checkbox por linha + selecionar página inteira, barra "Aplicar a N" com os 4 estados de triage — e exportação CSV do resultado filtrado
- **Findings**: busca por projeto/branch/commit, exportação CSV do histórico de scans
- **Projects**: busca por nome/linguagem (combina com o filtro Public/Private), exportação CSV
- Helper genérico `exportCSV()` com escaping correto e BOM UTF-8 (abre certo no Excel)

## [0.8.14] - 2026-08-30

### Added
- **Resize vertical e diagonal dos cards**: além da alça lateral (largura), cada card ganhou alça inferior (altura livre, 140–900px) e alça de canto (largura + altura juntos)
- Card com altura fixa vira flex-column: o conteúdo interno (tabela) ganha scroll próprio
- **Duplo clique** na alça inferior/canto volta à altura automática
- Alturas persistem em `localStorage` junto com ordem, visibilidade e larguras

## [0.8.13] - 2026-08-30

### Added
- **Redimensionamento dos cards do Dashboard**: no modo "⚙ Personalizar", cada card tem uma alça na borda direita — arraste para mudar a largura (2 a 6 colunas do grid, com snap por coluna e preview ao vivo); botões − / + no cabeçalho do card como alternativa; larguras persistem em `localStorage` junto com ordem e visibilidade

## [0.8.12] - 2026-08-30

### Added
- **Drag & drop nos cards do Dashboard**: no modo "⚙ Personalizar", arraste qualquer card e solte onde quiser — a reordenação acontece ao vivo durante o arrasto (cursor grab/grabbing, card translúcido com glow ciano enquanto arrastado); a nova ordem persiste em `localStorage` ao soltar

## [0.8.11] - 2026-08-30

### Added
- **Dashboard customizável**: botão "⚙ Personalizar" ativa modo de edição — cada card ganha controles ◀ ▶ (mover) e ✕ (remover); cards removidos podem ser re-adicionados pela barra "Adicionar"; layout persiste em `localStorage`

### Fixed
- **Botão de tema**: ícones sun/moon trocados de lucide para SVG inline fixo — o `lucide.createIcons()` re-processava os ícones a cada render e quebrava o toggle
- **Tema claro**: superfícies que ficavam escuras no light mode corrigidas (thead, filter bar, inputs, painéis de detalhe, pre.yaml, vis-tabs)
- **Card do usuário** voltou a ficar fixo no rodapé da sidebar (o `margin-top: auto` se perdeu no redesign)

## [0.8.10] - 2026-08-30

### Fixed
- Card "Tendência de findings" não estica mais para acompanhar a tabela ao lado (`align-items: start` nos grids) — cada card mantém a altura natural
- Tabela "Último scan por projeto" ganhou scroll interno (max 340px) com cabeçalho sticky — o Dashboard ficou compacto

## [0.8.9] - 2026-08-30

### Added
- **Ordenação em todas as tabelas**: clique no cabeçalho ordena (↑/↓ com seta indicadora ciano) — Dashboard "Último scan", Scans, Results, Findings, Projects e Allowlist
- Ordenação vive no estado (`_sort`) e sobrevive ao auto-refresh das abas Scans/Dashboard
- Severity na aba Results ordena por criticidade real (critical → info), não alfabeticamente
- **Cards do Dashboard finalmente populados**: "Projetos mais arriscados" (top 6 por critical+high abertos, clicável → Results), "Projetos mais seguros" (menos findings abertos) e "Distribuição por engine" (barras Semgrep/Trivy/Gitleaks) — estavam vazios desde a criação
- `/api/overview` agora retorna `risk` (findings abertos por repo) e `engines` (distribuição por ferramenta)

## [0.8.8] - 2026-08-30

### Changed
- **Sidebar redesenhada**: nav em pills arredondadas com margem (sem risco lateral colado na borda); item ativo com gradiente ciano, borda glow e dot luminoso à direita
- Linha de brilho vertical na borda direita da sidebar (gradiente que esvai)
- Brand maior (38px) com anel + glow; sub-label com tracking largo
- Área do usuário virou card: avatar circular com inicial do username, nome com ellipsis, logout como botão-ícone que fica vermelho no hover
- Footer minimalista em monospace centrado
- Helper `setUserUI()` unifica os 3 pontos que preenchiam nome/role e agora também a inicial do avatar

## [0.8.7] - 2026-08-30

### Added
- **Painel de detalhes do finding (aba Results)**: chevron ▶ (ou clique no nome da regra) expande painel completo com:
  - **Descrição** completa da engine
  - **Localização** com link direto para o arquivo e linha exata no GitHub (`blob/main/...#L<linha>`), repo, regra, engine e datas first/last seen
  - **Remediação sugerida** gerada por padrão da regra (SQLi, XSS, secrets, crypto fraca, path traversal, SSRF, deserialização, TLS, CVE/GHSA de dependência, etc.)
  - **Definition of Done** em checklist: correção mergeada, re-scan limpo, sem regressão — com itens extras para secrets (rotação obrigatória) e dependências (bump de versão)
  - **Referências**: NVD para CVEs, GitHub Advisory para GHSA, Semgrep Registry, Aqua Vuln DB e link do código
- Coluna Location da aba Results agora é link clicável para o GitHub na linha exata
- Arquivos na expansão da aba Findings também viraram links

## [0.8.6] - 2026-08-30

### Added
- **Aba Findings — expansão de detalhes**: botão **▼ Detalhes** em cada scan expande uma linha inline com os findings abertos do projeto (severidade, regra, arquivo:linha, engine, mensagem) — até 15 visíveis, com contagem do restante e link "Ver tudo em Results →" já filtrado pelo repo
- Guard de sessão em `loadVulns()` (retorno null da API não quebra mais a aba)

## [0.8.5] - 2026-08-30

### Changed
- **UI v2 — efeitos reais**: blobs de luz animados no fundo (`drift` 18s) + dot-grid em camada separada
- Stat strip virou **cards individuais** com gap, borda glow por severidade, hover com `translateY(-2px)` e sombra colorida
- Números das métricas em **Space Grotesk 36px/800** — critical com pulso de text-shadow contínuo
- Cards com glassmorphism real: `backdrop-filter: blur(14px)`, fundo translúcido, linha de brilho ciano no topo
- Sidebar e topbar translúcidos com blur; títulos de card/topbar em uppercase espaçado
- Login: blobs animados atrás do card, glassmorphism mais forte, título em Space Grotesk
- Fonte Space Grotesk adicionada (display numérico + brand)
- `prefers-reduced-motion` respeitado em todas as animações

### Fixed
- `addRepo()` valida o nome no cliente antes de enviar — submeter o form vazio não dispara mais `POST /api/repos` 400 no console; mostra mensagem inline e foca o campo

## [0.8.4] - 2026-08-30

### Changed
- **Redesign UI — Void Tech**: tema dark-first com fundo void `#04060c`, dot-grid CSS, glassmorphism nos cards e topbar (backdrop-filter blur)
- Accent trocado para ciano elétrico `#00b8ff` com glow nos elementos ativos
- Stat strip: números em JetBrains Mono 28px, critical com text-shadow pulsante
- Login: background com radiais cyan, card glassmorphism com scan-line animada
- Severity badges com border sutil e dot::before com box-shadow glow no critical
- Status badge `running` com pulso glow (`box-shadow: 0 0 6px currentColor`)
- Nav active: border-left ciano + `box-shadow inset` para efeito de glow lateral
- Brand icon: `linear-gradient(135deg, #00b8ff → #0070cc)` + box-shadow glow
- Botão primary: gradiente + `box-shadow: 0 2px 12px rgba(0,184,255,.3)` com hover brilho
- Topbar badge critical: animated `crit-glow` keyframe + font monospace
- Tab pane transitions com `fade-in` keyframe

## [0.8.3] - 2026-08-30

### Added
- **Auto-registro do tunnel URL**: ao subir, o dashboard detecta a URL do quick tunnel via `http://cloudflared:20241/quicktunnel` e atualiza automaticamente a variável `SECPIPE_DASHBOARD_URL` no GitHub via API — sem precisar copiar a URL manualmente a cada restart
- `--metrics 0.0.0.0:20241` adicionado ao comando cloudflared para expor o endpoint `/quicktunnel` na rede interna Docker

## [0.8.2] - 2026-08-30

### Fixed
- Step "Enviar para o dashboard" no workflow não bloqueia mais o Policy Gate quando o upload falha (adicionado `continue-on-error: true`)
- `upload.py` agora imprime o código HTTP e body da resposta em caso de erro 4xx/5xx, facilitando diagnóstico

## [0.8.1] - 2026-08-29

### Added
- **Aba Scans — motivo da falha**: botão **Why?** aparece em runs com `failure`/`timed_out`; expande uma linha inline com cada job, ícone de resultado (✓/✗/⏱) e os steps que falharam com link para o log no GitHub
- **Backend `GET /api/runs/{repo}/{run_id}/jobs`**: consulta GitHub Jobs API, retorna nome do job, conclusão e steps com falha

### Fixed
- CDN Lucide trocado de `jsdelivr` para `unpkg` (path UMD correto); `createIcons()` protegido com guard `typeof lucide !== 'undefined'`

## [0.8.0] - 2026-08-29

### Added
- **Lucide icons**: todos os ícones da UI migrados para Lucide (via CDN `lucide@0.263.1`) — nav, topbar, sidebar e tabelas dinâmicas
- **Theme toggle dark/light**: botão sun/moon no topbar persiste preferência em `localStorage`, respeita `prefers-color-scheme` como padrão
- `lucide.createIcons()` chamado no boot e após cada render dinâmico (`renderRepos`, `loadJobs`, `renderFindPage`, `loadVulns`)

### Changed
- Botão "Atualizar" no topbar usa ícone `refresh-cw` no lugar do SVG inline
- Empty-state de Findings usa ícone `circle-check-big` no lugar do SVG inline
- Ícone de repo na tabela Projects usa `folder-git-2` no lugar do SVG inline

## [0.7.0] - 2026-08-29

### Added
- **2FA/MFA TOTP**: autenticação de dois fatores via Google Authenticator, Authy, 1Password, etc.
- Login em dois passos: senha → código TOTP de 6 dígitos (se 2FA ativo)
- Sessão parcial com TTL de 5 min entre os dois passos
- Rate limiting específico para tentativas de TOTP (5 tentativas → 5 min lockout)
- Endpoints: `GET /api/auth/totp/status`, `/setup`, `POST /confirm`, `/activate`, `/disable`
- Seção "2FA" no Settings: QR code via qrcodejs, ativação com confirmação de código, desativação com senha
- `pyotp>=2.9` adicionado ao requirements.txt (pure Python, sem C extensions)

## [0.6.1] - 2026-08-29

### Fixed
- Login screen agora funciona: `initApp()` verifica `/api/auth/me` antes de carregar o dashboard
- `api()` helper redireciona para tela de login ao receber 401
- `loadDashboard()` trata retorno `null` (evita crash `Object.values(null)`)
- `doLogin()` / `doLogout()` implementados com feedback de erro e loading
- `applyRole()` aplica visibilidade de elementos `[data-min-role]` por role
- Favicon 404 resolvido com rota `/favicon.ico` inline no backend

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
