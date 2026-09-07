# Changelog

All notable changes to SecPipe are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.15.10] - 2026-09-07 20:53

### Changed
- **Card "Total abertos" agora mostra a nota `· N info`**: o total inclui findings de severidade `info` (hoje 397, todos do `shadow-api-scanner`), que não têm card próprio — sem a nota, os quatro cards visíveis (2 + 335 + 463 + 41 = 841) não fechavam com o 1238 e o total parecia errado. Tooltip do card também explica

### Execução — auditoria das métricas (pedido: "verifique se as métricas estão corretas e atualizando")
- Recalculadas direto no SQLite e comparadas com `/api/overview`: critical 2, high 335, medium 463, low 41, info 397, total 1238, corrigidos 24, aguardando PR 0, SLA estourado 2 (recalculado com a política 7/30/90/180 dias) — **9/9 iguais**; `last_scans`/`risk_scores` cobrem os 44 repos; `by_status` = 1238 open / 24 fixed / 1 false_positive
- Valores **na tela** (Playwright, aba Dashboard): 1238 / 2 / 335 / 463 / 41 / 24 / 2 / 0, pill "2 Critical", badges Scans 14 · Findings 1238 · PRs 2, 44 linhas em "Último scan por projeto" — idênticos à API
- Freshness: um finding `low` marcado como `accepted` direto no banco → overview caiu para 40 na chamada seguinte; revertido → 41. Atualização é imediata (sem cache no overview)
- Badge Scans: `/api/runs` traz os 30 runs mais recentes (20 repos); 14–16 têm o último run em falha — quase todos pelo Scorecard/gcr.io corrigido na v0.15.3; limpa conforme novos scans rodam. Badge PRs usa cache de 60 s
- Rebuild `docker compose up -d --build` — healthy

## [0.15.9] - 2026-09-07 20:40

### Fixed
- **"Mesclar" devolvia 405 cru em PR com conflito**: o GitHub responde `405 Pull Request is not mergeable` quando a base mudou depois da correção. O backend agora pré-checa `mergeable`/`mergeable_state`/`draft`/`state` e responde **409 com explicação** ("PR #n está em conflito com a base — use Refazer…"); um 405 tardio do GitHub é traduzido para a mesma mensagem

### Added
- **Refazer** (`POST /api/ai/prs/{repo}/{n}/redo`, analyst): para PR em conflito com finding vinculado, fecha a PR obsoleta, limpa o estado da correção e dispara um novo autofix do mesmo finding a partir da base atual; a nova PR aparece na fila ao terminar (job rastreado no drawer de IA). Substitui o botão Mesclar nessas linhas
- **Dicas por linha** na coluna Estado: "Base mudou — use Refazer", ou "Finding já corrigido na base — feche esta PR" (quando o finding vinculado já está `fixed`; nesse caso Mesclar fica desabilitado e Fechar vira a ação principal)

### Execução
- Rebuild `docker compose up -d --build` — healthy · `py_compile` OK · `node --check` OK
- Testes no container (sem efeito colateral — nenhuma PR mesclada/fechada): `merge #10` e `merge #8` (Tyr-Red-Team-Agent, em conflito) → **409** com a mensagem nova; `redo #10` (finding já `fixed`) → 409 "basta fechar"; `redo #8` (sem vínculo) → 400 "use Sincronizar PRs"
- Verificação visual no Playwright da aba com os novos botões/dicas
- Observação: a fila caiu de 9 para 6 PRs abertas entre as leituras — fechamentos feitos pelo usuário no GitHub, não pelo SecPipe (audit log sem `ai_pr_closed`)

## [0.15.8] - 2026-09-07 20:37

### Fixed
- **Ícones nunca renderizavam (sidebar, botões, KPIs, tabelas)** — causa raiz: o `<script src="https://unpkg.com/lucide@0.263.1/…">` apontava para uma **versão que não existe no npm** (só há 0.263.0); o CDN respondia 404, `lucide` ficava indefinido e todas as chamadas `if (typeof lucide !== 'undefined') lucide.createIcons()` falhavam em silêncio desde o início do projeto. O Lucide agora é **servido localmente** (`dashboard/static/vendor/lucide.min.js`, v1.42.0, ISC) pela nova rota `app.mount("/static", …)` — sem dependência de CDN
- **Aba Pull Requests — coluna de ações cortada**: tabela com `min-width` (1180 px / 900 px) dentro do wrapper de rolagem horizontal e larguras fixas para PR e ações; título da PR (que duplicava a regra do finding) removido da coluna PR, mantido como tooltip do `#número`

### Execução
- Diagnóstico: extensão do Chrome bloqueada para ações, então o dashboard foi aberto no **Playwright** (headless): `typeof lucide` → `"lucide is not defined"`; `curl unpkg.com/lucide@0.263.1` → "Package version not found"; registry npm consultado (latest 1.42.0, 673 versões)
- Cobertura: os UMDs 0.263.0 e 1.42.0 foram executados no Node e cruzados com os 73 nomes `data-lucide` usados no HTML — 0.263.0 deixava 12 sem ícone (`triangle-alert`, `circle-alert`, `clock-alert`, `loader-circle`, `circle-check`, `circle-x`…); **1.42.0 cobre 100%**
- Rebuild `docker compose up -d --build` — healthy · `py_compile` OK · CSS validado com tinycss2 (476 regras, 0 erros) · HTML balanceado
- Verificação no Playwright após o rebuild: `lucide` carregado com 2070 ícones, **0 `<i data-lucide>` pendentes, 112 SVGs renderizados** (8 na sidebar, 8 na stat-strip); screenshot da aba Pull Requests com 9 PRs, ícones em todos os botões e coluna de ações completa
- `.playwright-mcp/` adicionado ao `.gitignore`; screenshots temporários removidos da raiz do repo

## [0.15.7] - 2026-09-07 20:27

### Added
- **Aba "Pull Requests"** (sidebar, com badge de contagem): fila das PRs da IA abertas em todos os repos, lida direto do GitHub — projeto, número/título/branch→base, finding vinculado (severidade, regra, arquivo:linha), tamanho do diff (+/−, arquivos), **estado de merge** (pronta / checks falharam / aguardando checks / conflito / rascunho) e data. Filtros por projeto, estado e busca; botão *Atualizar* força nova leitura
- **Ações por PR** (role analyst): **Mesclar** (squash via API, desabilitado em conflito/rascunho) e **Fechar** sem mesclar — ambas com confirmação e registro no Audit Log (`ai_pr_merged` / `ai_pr_closed`); link direto para o GitHub
- **Seção "Branches prontas sem PR"**: correções preparadas pelo piloto automático (`fix_branch` sem `fix_pr`) com botão **Abrir PR**
- **`GET /api/ai/prs`** usa a Search API do GitHub (`"[SecPipe AI] fix" in:title is:pr is:open user:<owner>`) — uma chamada para todos os repos — e enriquece cada PR com `GET /pulls/{n}` (mergeable_state, additions…); cache de 60 s no servidor; `?refresh=1` ignora o cache. **`POST /api/ai/prs/{repo}/{n}/merge`** e **`/close`**. Helpers `_gh_headers()` / `_gh_json()` introduzidos

### Execução
- Rebuild `docker compose up -d --build` — healthy · `py_compile` OK · `node --check` OK · 7 ids novos únicos · `_currentRole` reutilizado para esconder ações de viewer
- `GET /api/ai/prs?refresh=1` no container: **14 PRs da IA abertas** em 9,0 s (Tyr-Red-Team-Agent 6, tyr 4, mdm 3, OmniHook 1 — este último não estava na varredura anterior, a Search API o encontrou); estados: 5 *checks falharam*, 3 *conflito*, 6 *calculando*; 10/14 com finding vinculado; segunda chamada servida do cache em 0,00 s; merge de PR inexistente → 404 propagado do GitHub
- Nenhuma PR foi mesclada ou fechada — apenas leitura
- Não verificado: visual da aba (extensão do browser bloqueada)

## [0.15.6] - 2026-09-07 20:17

### Added
- **Sincronizar PRs da IA (Findings › Correções)**: `POST /api/ai/sync-prs` (analyst) lê as PRs `secpipe/ai-fix-*` de todos os repos via GitHub API, extrai a regra/arquivo do título `[SecPipe AI] fix: …` e vincula ao finding correspondente (`fix_branch`, `fix_pr`, `fix_at`), preferindo a PR aberta mais recente por regra e nunca sobrescrevendo vínculo existente. Botão **Sincronizar PRs** na barra de filtros. Necessário porque o vínculo PR↔finding só passou a ser gravado na v0.14.19 — as PRs anteriores existiam no GitHub mas eram invisíveis no dashboard

### Execução
- Diagnóstico da queixa "não vi correção da IA": motor ativo (Claude Code OAuth, CLI 2.1.263, `GITHUB_TOKEN` ok); audit log com 15 disparos manuais de `ai_autofix`; consulta ao GitHub encontrou **25 PRs da IA já abertas** (tyr 7, mdm 6, web-fr1da 2, Tyr-Red-Team-Agent 10), 12 ainda em aberto — o problema era de visibilidade, não de execução
- Autofix disparado ao vivo via `POST /api/ai/autofix` no finding `dockerfile.security.missing-user-entrypoint` (Tyr-Red-Team-Agent, `Dockerfile:54`): concluído em **30 s**, branch `secpipe/ai-fix-1788822987`, **PR #11** aberta com diff de 6 linhas (`useradd -r -u 1001`, `chown -R`, `USER appuser` antes do `ENTRYPOINT`); estado gravado no finding pelo fluxo normal
- Rebuild `docker compose up -d --build` — healthy · `py_compile` OK · `node --check` OK
- `POST /api/ai/sync-prs`: 45 repos varridos, **29 PRs da IA encontradas, 18 findings vinculados**; Correções passou de 24 para **43 itens** (19 `ai_pr` + 24 `scan`)
- Limpeza: repo fictício `x/y` (sobra de um teste de auth da v0.14.14) removido de `repos/scans/findings/audit_log`
- Não verificado: visual do botão e dos chips "PR" (extensão do browser bloqueada)

## [0.15.5] - 2026-09-07 20:10

### Fixed
- **Motivo da falha "não chegava ao front"**: chegava, mas escondido atrás de um ícone `?` de 16px na última coluna da aba Scans. O ícone virou um botão vermelho visível **"Motivo"** (com estados *Carregando…* / *Fechar*) em todo run com falha; o tooltip explica que abre gate reprovado ou erro técnico
- **Parser do gate cortava a engine "Semgrep OSS"**: o regex assumia engine de uma palavra e jogava "OSS" para dentro da regra. Agora a regra é o último token antes do `—` (nunca tem espaço) e a engine fica com o resto

### Execução
- Antes da correção, `GET /api/runs/k19x/Tyr-Red-Team-Agent/34168500815/jobs` já devolvia `source: log`, violação `37 finding(s) >= high`, totais `198 ativos / high 37 / medium 16 / low 4 / info 141` e **10 findings bloqueantes** — confirmando que o problema era de descoberta na UI, não de dados
- Rebuild `docker compose up -d --build` — container healthy · `py_compile` OK · `node --check` OK
- Re-teste do mesmo run após o fix do regex: engine `Semgrep OSS` / regra `dockerfile.security.missing-user-entrypoint…` separadas corretamente
- Não verificado: visual — pedido ao usuário para abrir o "Motivo" do run e validar

## [0.15.4] - 2026-09-07 20:03

### Added
- **Motivo de falha técnica por job (aba Scans › detalhe do run)**: para cada job que falhou por motivo não relacionado ao gate, o backend baixa o log e extrai as linhas de erro (`##[error]`, `Error:`, `exit code`, `denied`, `timed out`…; fallback: últimas 6 linhas) — exibidas em um bloco vermelho logo abaixo do job. Máximo de 3 downloads de log por run
- **Aviso "Falha técnica"** quando o run falhou sem gate reprovado: lista os jobs afetados, explica que não é bloqueio de política e oferece atalho para os findings do projeto (que continuam válidos do último scan bem-sucedido)
- Detecção do job de gate aceita o nome prefixado pelo workflow reutilizável (`scan / Policy Gate`)

### Execução
- Rebuild `docker compose up -d --build` — container healthy
- `py_compile` OK · `node --check` OK
- Teste contra os 4 runs falhos mais recentes via `GET /api/runs/{repo}/{run}/jobs`: nos 3 runs de hoje (Tyr-Red-Team-Agent ×2, AES_DECODE_ENCODE_JS) o job *Supply Chain (OSSF Scorecard)* traz o motivo exato — `Error response from daemon: Head "https://gcr.io/v2/openssf/scorecard-action/manifests/v2.4.0": denied` → `Docker pull failed with exit code 1`; nos runs que também reprovaram o gate, `gate_context` continua presente com os findings bloqueantes
- Não verificado: visual (extensão do browser bloqueada nesta sessão)

## [0.15.3] - 2026-09-07 20:00

### Fixed
- **Job "Supply Chain (OSSF Scorecard)" falhando com `Docker pull failed with exit code 1`**: o `ossf/scorecard-action@v2.4.0` (jul/2024) puxa `gcr.io/openssf/scorecard-action`, registro do Google que foi desativado. Pin atualizado para **v2.4.4** (jul/2026), que usa `ghcr.io/ossf/scorecard-action`. O `continue-on-error: true` do step não cobria o erro porque o pull da imagem acontece na preparação do job, antes do step rodar

### Execução
- Consultada a API do GitHub: releases do `ossf/scorecard-action` — v2.4.4 (2026-07-23) é a mais recente; `action.yaml` em `main` confirma `image: docker://ghcr.io/ossf/scorecard-action:v2.4.4`
- Alteração só em `.github/workflows/security-scan.yml` (workflow reutilizável) — sem rebuild do container; como os repos chamam `k19x/ci_cd/.github/workflows/security-scan.yml@main`, o fix vale para todos assim que o push entra
- Não executado: um scan real para confirmar o pull — basta re-rodar o job que falhou (o run usa o workflow atual de `main`)

## [0.15.2] - 2026-09-07 19:53

### Added
- **Correções › bloco Geral**: painel de totais no topo da sub-aba — total de correções, projetos, últimos 7 e 30 dias; por origem (scan / IA / triagem manual); por severidade (critical / high / medium / low). Recalculado a cada filtro aplicado
- **Correções › visão "Geral (cronológico)"**: seletor *Por projeto / Geral* — a segunda lista todas as correções numa única tabela ordenada por data, com coluna Projeto. A visão por projeto continua sendo o padrão
- Render das linhas extraído para `_fixRow(f, withRepo)`, compartilhado pelas duas visões

### Execução
- Rebuild `docker compose up -d --build` — container healthy
- `node --check` no JS inline OK; variáveis CSS `--high/--med/--low/--crit/--ok/--accent` usadas nos KPIs confirmadas no `:root`; ids `fxView` e `fxSummary` únicos
- Sem mudança de backend: reaproveita `GET /api/fixes` (24 correções reais em 4 projetos, validado na v0.15.1)
- Não verificado: visual (extensão do browser bloqueada nesta sessão)

## [0.15.1] - 2026-09-07 19:50

### Added
- **Findings › sub-aba "Correções"**: linha do tempo de tudo que foi corrigido, agrupada por projeto, com data/hora local, tipo, finding (severidade, regra, arquivo:linha, engine) e detalhes (branch, link da PR, resumo da IA, quem fez). Três origens unificadas: **corrigido no scan** (finding sumiu no ingest), **correção da IA** (branch pronta / PR aberta) e **triagem manual** (corrigido, não explorável, risco aceito). Filtros por projeto, tipo e busca livre; exportação CSV
- **`GET /api/fixes?repo=`** (viewer): une `findings` (status `fixed` ou `fix_branch`) com `audit_log` (ação `triage`), ordenado por data desc, limite 500 (máx. 2000)
- Cards do Dashboard que levam a Findings agora garantem a sub-aba "Findings" ativa (`goToFindings` → `showFindingsTab('list')`)

### Execução
- Rebuild `docker compose up -d --build` — container healthy
- `py_compile app.py` OK · `node --check` no JS inline OK · 7 ids novos únicos no DOM
- Teste de API no container: `GET /api/fixes` → 200, **24 correções reais em 4 projetos** (Tyr-Red-Team-Agent 9, web-fr1da 9, tyr 5), todas do tipo `scan` (ainda não há correções da IA nem triagem manual no banco); ordenação desc confirmada; `?repo=k19x/tyr` → 5 itens, todos do repo
- Não verificado: visual da sub-aba (extensão do browser bloqueada nesta sessão)

## [0.15.0] - 2026-09-07 19:43

### Execução
- Rebuild do container `ci_cd-dashboard-1` via `docker compose up -d --build` (healthy)
- Migração automática no boot: `ALTER TABLE repos ADD COLUMN autopilot` — confirmada via `PRAGMA table_info(repos)`
- Testes de API dentro do container (sessão admin + key `ingest` temporária): 10/10 passaram — `overview.fix_ready`, `GET /api/users`, `PUT /api/repos/{name}/autopilot` (400 para modo inválido, 200 para `off`), `GET /api/repos` expõe `autopilot`, override `global ON + repo OFF → 0 enfileirados`, `global OFF + repo ON → 1 enfileirado`
- `python -m py_compile app.py` OK; `node --check` no JS inline OK; varredura de ids duplicados e de referências às funções da aba Jobs removida — zero órfãos após limpar o CSS morto
- Limpeza: repo fictício `secpipe/ap-override-test` (findings/scans/audit/repos) e key temporária removidos; global do piloto restaurado ao padrão (desligado / critical / 3)
- Não verificado: visual das novas telas (extensão do browser bloqueada nesta sessão) — pendente de confirmação pelo usuário

### Changed — reorganização dos menus
- **Abas renomeadas**: *Results* → **Findings** (vulnerabilidades com triagem) e *Findings* → **Histórico de scans** (scans ingeridos com totais C/H/M/L). Os nomes estavam invertidos em relação ao conteúdo e causavam confusão ("Findings mostra 8 criticals, Dashboard mostra 2")
- **Aba Jobs removida**: era um recorte de 2h da aba Scans mais um espelho do drawer de jobs de IA da topbar. O filtro de status em Scans (running/queued/failure…) cobre o caso; ~130 linhas de código duplicado retiradas
- **Settings reorganizado em 5 sub-abas**: *Policy* (limites, allowlist, SLA) · **Segurança** (novo: 2FA, Usuários, API keys) · *AI* (modelo + piloto automático) · *Notificações* · *Developer* (só documentação da API). O 2FA saiu de dentro de Policy; as API keys saíram de Developer
- **Revogar API key** agora usa o `uiConfirm` do app em vez do `confirm()` nativo do browser

### Added
- **Tela de Usuários** (Settings → Segurança, admin): lista, cria (usuário/senha/papel), remove e redefine senha inline — sobre o CRUD `/api/users` que já existia no backend sem interface
- **Fila de correções**: filtro **Correção pronta** em Findings e card **Aguardando PR** no Dashboard (`overview.fix_ready`), ambos levando direto aos findings com branch da IA pronta
- **Piloto automático por projeto**: coluna *Piloto* em Projects com `global / ligado / desligado` (`PUT /api/repos/{name}/autopilot`); `off` sempre vence, `on` liga mesmo com o global desligado. Coluna `repos.autopilot`
- **Badges na sidebar**: Scans mostra quantos projetos têm o último run em falha; Findings mostra o total de findings abertos

## [0.14.19] - 2026-09-07

### Added
- **Piloto automático (Settings → AI)**: a cada ingest, findings novos acima da severidade mínima configurada (critical/high/medium) entram numa fila serial onde a IA clona o repo, corrige o código e faz push da branch `secpipe/ai-fix-…` — **sem abrir a PR**. Teto por scan configurável (1–10, padrão 3); só findings com arquivo associado são elegíveis; cada execução vira evento `ai_autofix_auto` / `ai_autofix_auto_error` no Audit Log com usuário `autopilot`
- **Chip "branch pronta" / "PR" em Results**: findings com correção pronta mostram o estado ao lado da regra; o detalhe exibe a branch, a data e o resumo da IA, com botão **Abrir PR** (role analyst) que cria o Pull Request a partir da branch já enviada
- **`/api/ai/autopilot`** (GET/PUT, admin para gravar) e **`POST /api/ai/open-pr`** `{repo, fid}`; ingest responde `autopilot_queued`
- Colunas `fix_branch`, `fix_pr`, `fix_summary`, `fix_at` em `findings`

### Changed
- `_do_autofix(r, open_pr=True)`: criação do PR extraída para `_open_fix_pr()`, reutilizada pelo botão manual "Aplicar correção" e pelo "Abrir PR" do piloto; estado da correção persistido no finding em ambos os fluxos

## [0.14.18] - 2026-09-07

### Fixed
- **Drawer "Jobs de IA" transparente**: o painel usava `background:var(--card)`, mas `--card` nunca foi definida — o fundo resolvia para transparente e o conteúdo do dashboard vazava por trás. Definido `--card: var(--s1)` no `:root` (opaco, acompanha o tema claro/escuro); corrige também o banner de gate reprovado, que usava a mesma variável

## [0.14.17] - 2026-09-07

### Fixed
- **Ícone "Jobs de IA" invisível**: o `<i data-lucide="cpu">` da topbar não renderizava de forma confiável; substituído pelo SVG inline do próprio glyph Lucide `cpu`, mesmo padrão já usado pelo toggle de tema ao lado, eliminando a dependência do timing de `createIcons()`

## [0.14.16] - 2026-09-07

### Fixed
- **Ícone "Jobs de IA" na topbar**: o badge de contagem tinha `display:flex` inline, que anulava o atributo `hidden` — o círculo vermelho vazio ficava sempre visível por cima do ícone `cpu`. Estilo movido para a classe `.aj-badge` com regra `[hidden]{display:none}`; badge reposicionado no canto (`-5px`) para não cobrir o ícone

## [0.14.15] - 2026-09-07

### Fixed
- **SQLite sob concorrência**: `db()` abre conexões com `timeout=10` e `init_db()` ativa `PRAGMA journal_mode=WAL` — threads de notificação/IA e o `UPDATE last_used` das API keys não geram mais `database is locked`
- **`api()` no frontend**: agora trata falha de rede, corpo não-JSON e `!res.ok`; retorna `null` em qualquer erro (em vez de devolver `{detail}` como se fosse dado, o que quebrava `data.keys.length` para viewers) e exibe a mensagem em um toast
- **Callers sem null-check**: `loadTrend` e `loadFindings` não quebram mais quando `api()` retorna `null`

### Added
- **Gate reprovado — findings bloqueantes no painel**: ao expandir um scan com falha na aba Scans, o detalhe agora lista as violações (`3 finding(s) com severidade >= high`) e os findings que o gate de fato viu — severidade, engine, regra, arquivo:linha e mensagem — mais os totais (ativos / allowlisted / por severidade). O backend baixa o log do job Policy Gate via GitHub API (`/actions/jobs/{id}/logs`, seguindo o redirect para o blob sem credenciais) e parseia a saída do `scripts/gate.py`; se o log expirou ou está indisponível, cai para os critical/high abertos no banco e avisa (`source: db`)
- **`uiToast(msg, kind)`**: notificação discreta no rodapé (4,5s), usada por `api()` para erros sem interromper auto-refresh com modais

## [0.14.14] - 2026-09-07

### Security
- **`/api/ingest` fail-closed**: `check_token` agora exige `X-API-Key` sempre; sem `SECPIPE_TOKEN` no `.env` o endpoint rejeitava nada e aceitava qualquer payload. Comparação do token legado com `compare_digest`; `last_used` atualizado também para keys de ingest
- **API keys nunca viram admin**: scope `admin` mapeia para role `analyst` (triagem, scans, policy); `require_role("admin")` rejeita qualquer principal de API key — gestão de usuários e keys exige sessão humana. Scope `ingest` só é aceito em `/api/ingest` (403 nos demais endpoints)
- **XSS em handlers inline**: novo helper `jsq()` (JSON.stringify + esc) substitui o padrão `onclick="fn('${esc(x)}')"` em 14 pontos — o parser HTML decodificava `&#39;` de volta para `'` antes do JS rodar, permitindo injeção via nome de API key, repo ou finding id. Removidos os `replace(/'/g,"\\'")` manuais (`repoKey`, `nameKey`)
- **Escape em campos esquecidos**: `r.detail` (autofix/verify), `f.rule` no `uiConfirm`, e `href` de `fileLink()` (agora com `encodeURIComponent` por segmento)

### Changed
- Developer tab: descrição do scope `admin` atualizada para refletir o novo limite

## [0.14.13] - 2026-08-30

### Changed
- **Developer tab — padding**: `card-body` dos cards API Keys e Referência da API recebeu `padding: 0 18px 18px` para respiração lateral do conteúdo

## [0.14.12] - 2026-08-30

### Changed
- **Developer tab — redesign completo**: scope selector substituído por radio cards visuais (read/ingest/admin com cor, label e descrição); empty state da tabela com ícone; "Nunca" em vez de "—" para último uso; badges de escopo coloridos (verde/amarelo/vermelho); botão de fechar no alerta da chave gerada
- **Referência da API**: seção "Quick Start" com snippet curl copiável; endpoints expandidos de 4 para 7 (adicionados `/api/repos`, `/api/projects`, `/api/keys`), cada um com descrição inline e badge de escopo colorido; links para Swagger UI e ReDoc

## [0.14.11] - 2026-08-30

### Added
- **API Keys — gerenciamento completo**: nova sub-aba "Developer" em Settings com criação, listagem e revogação de API keys; token `secpipe_...` exibido uma única vez após criação com botão de cópia
- **API Keys — autenticação dual**: endpoints protegidos aceitam `X-API-Key: <token>` além do cookie de sessão; escopos `read`, `ingest` e `admin` com mapeamento para roles existentes
- **API Keys — documentação inline**: seção "Documentação da API" com exemplos de endpoints e links para `/docs` (Swagger) e `/redoc`
- **`/api/keys` CRUD**: `GET` lista keys, `POST` cria (retorna plaintext uma vez), `DELETE /{id}` revoga; todas requerem role `admin`
- **Audit log para keys**: eventos `api_key_created` e `api_key_revoked` registrados automaticamente

## [0.14.10] - 2026-08-30

### Changed
- **Results — coluna Project**: adicionada coluna dedicada "Project" com o nome do repo em cada linha da tabela; coluna é ordenável; subtexto redundante removido da coluna Vulnerability

## [0.14.9] - 2026-08-30

### Changed
- **Sidebar icons atualizados**: ícones Lucide revisados para melhor semântica (scan, briefcase, list, search, folder, settings)
- **KPI cards com ícones**: cada card da stat-strip agora exibe um ícone Lucide à esquerda do label (package-open, shield-alert, triangle-alert, circle-alert, info, check-circle, clock-alert)
- **Jobs tab — painel "Scans GitHub Actions"**: ícone do header substituído por `github` para melhor identificação
- **Botão "Why?" em Scans**: substituído pelo ícone `help-circle` mais discreto; sem fundo vermelho
- **Coluna Vulnerability em Results**: truncamento com ellipsis + tooltip e badge do repositório abaixo do nome da regra
- **OWASP Top 10**: "Sem categoria" removido do gráfico de barras; exibido como aviso textual abaixo
- **Tendência de findings**: exibe mensagem de estado vazio quando há menos de 2 pontos de dados
- **Projects — ações em kebab menu**: os 3 botões de ação (Scan, Atualizar, Remover) foram consolidados em um dropdown `⋮` por linha
- **Projects — "Add project" como modal**: formulário inline substituído por um `<dialog>` nativo acessível; botão "Adicionar" na barra da aba abre o modal
- **Audit Log**: hashes longos (>20 chars hex) truncados a 7 chars com full hash no `title`; eventos consecutivos com mesma ação/minuto/usuário agrupados com badge `×N`
- **Settings — botões de salvar com ícones**: "Salvar policy.yml" → ícone `save`; "Commit & Push" → ícone `git-commit-horizontal`
- **Settings — sub-tab ativo**: borda inferior `2px solid var(--accent)` no tab ativo
- **Findings C/H/M/L**: headers com `title` e cor de severidade

## [0.14.8] - 2026-08-30

### Changed
- **Aba Jobs layout lado a lado**: Scans GitHub Actions e IA Engine agora ficam em grid 2 colunas (1fr / 1fr) com scroll independente por coluna (max-height 70vh), em vez de empilhados verticalmente

## [0.14.7] - 2026-08-30

### Fixed
- **Jobs tracker sempre mostrava "Concluído." para autofixes e verificações**: `pollAiJobTracked` passava string vazia para `aiJobDone`, que pelo fallback `|| 'Concluído.'` descartava o resultado real (link do PR ou "IA não fez alterações."); corrigido adicionando parâmetro `resultFmt` opcional à função e definindo `_fmtAutofix` e `_fmtVerify` para formatar corretamente o resultado nos jobs de autofix e verificação

### Added
- **Frontend redesign**: melhorias visuais nos KPI cards (gradient de severidade nas bordas, números 42px), sidebar com separadores entre seções, tabelas com mais padding, hover glow nos cards, font-rendering antialiased, `@keyframes spin` adicionado; IA Engine drawer usa Lucide icons ao invés de emojis

## [0.14.6] - 2026-08-30

### Added
- **Scan all projects com 1 clique**: botão "Scan all" na aba Projects dispara o `security.yml` em todos os repos cadastrados em paralelo (até 6 simultâneos); exibe feedback `X/Y repos disparados` em verde/amarelo por 6s; requer role analyst; backend `POST /api/runs/dispatch-all` com ThreadPoolExecutor e resultado por repo

## [0.14.5] - 2026-08-30

### Added
- **Aba Jobs**: nova aba na sidebar que agrega em um único lugar todos os serviços em andamento — seção "Scans GitHub Actions" (runs in_progress/queued e recentes das últimas 2h, com status visual colorido, link para o GitHub e tempo relativo) e seção "IA Engine" (jobs de autofix, sugestão, verificação e diagnóstico do localStorage, com ícone por tipo, status animado e tempo decorrido); badge vermelho na nav indica quantidade de jobs IA em andamento; auto-refresh a cada 10s enquanto a aba está ativa
- **Shadow API detection** (`scripts/shadow_api.py`): analisa estaticamente qualquer repo buscando definições de rota em 12 frameworks (FastAPI, Flask, Django, Express, Fastify, Hapi, Gin, Chi, Spring, Rails, Laravel, Actix, ASP.NET); quando há spec OpenAPI no repo, compara e emite `warning` para endpoints não documentados; sem spec, emite `note` informacional; integrado no job `api-security` do workflow com output `sarif-shadow`

## [0.14.4] - 2026-08-30

### Added
- **AI Jobs Tracker com persistência em localStorage**: jobs de IA (autofix, sugestão, verificação, diagnóstico) agora persistem entre reloads de página e trocas de aba — um widget na topbar (ícone CPU) exibe badge com contagem de jobs em andamento e drawer listando o histórico recente (até 1h); ao relogar, jobs com status "running" têm o polling retomado automaticamente via `aiJobsResumeAll()`; jobs concluídos ficam acessíveis no drawer até expirar
- **Scrollbars temáticos**: trilha e thumb dos scrollbars seguem as variáveis CSS do tema atual (claro/escuro/system) via `scrollbar-color` e `::webkit-scrollbar`

### Fixed
- **`aiJobsResumeAll()` chamado em todos os paths de login**: o resumo de jobs perdidos agora é chamado nos três caminhos de autenticação (login direto, login com 2FA na primeira etapa, e verificação TOTP)

## [0.14.3] - 2026-08-30

### Added
- **Sugestão de correção em cascata pós-autofix**: após um autofix aplicado com sucesso, a aplicação busca automaticamente outros criticals abertos no mesmo repo e os lista abaixo do PR, cada um com botão "Corrigir" individual — permitindo corrigir todos os criticals bloqueadores em sequência sem sair da tela; ao corrigir um segundo critical, a lista é atualizada recursivamente; ao zerar os criticals, exibe confirmação "Nenhum outro critical aberto neste repo"

## [0.14.2] - 2026-08-30

### Fixed
- **Why? panel destruído pelo auto-refresh**: aba Scans regenerava `tbody.innerHTML` a cada 15s mesmo com painel aberto — corrigido com guard (`if document.querySelector('.job-detail-row') return`) antes de regenerar
- **Gate failure sem contexto no PR**: ao falhar no "Enforce gate", o painel Why? agora exibe banner vermelho explicativo distinguindo PR bloqueada por findings pré-existentes (Trivy/Gitleaks não são diff-aware) de failure no push; inclui contagem de criticals/highs abertos no repo via DB local

### Added
- **`gate_context` no `/api/runs/{repo}/{run_id}/jobs`**: quando o gate falha, o backend retorna `{critical_count, high_count, is_pr, note, branch}` consultando o banco local — o frontend renderiza o banner automaticamente

## [0.14.1] - 2026-08-30

### Fixed
- **Vulns corrigidas não sumiam do dashboard**: o `vars.SECPIPE_DASHBOARD_URL` é resolvido no contexto do repo CALLER — o auto-registro do tunnel só atualizava o `k19x/ci_cd`, então os demais repos enviavam findings para a URL morta do tunnel anterior (upload falhava em silêncio e o estado nunca refrescava). O auto-registro agora atualiza a variável em **todos os repos cadastrados** (repos + scans do banco) a cada boot
- **Upload não roda mais em PRs**: com o Semgrep diff-only, o findings.json de PR é parcial e corromperia o estado do dashboard (falsos Fixed em massa)

## [0.14.0] - 2026-08-30

### Added — 6 features enterprise
- **Audit Log**: nova aba (admin) com todos os eventos — login, triagem, projetos add/del, scan disparado, policy push, mudança de modelo IA, autofix, SLA e notificações alteradas; busca, ordenação e CSV; tabela `audit_log` no SQLite
- **SLA de findings**: limites configuráveis por severidade (padrão: critical 7d, high 30d, medium 90d, low 180d) em Settings→Policy; card "SLA estourado" na stat strip do Dashboard; filtro "SLA estourado" e destaque vermelho + ícone alarm-clock (com idade em dias) nas linhas do Results; contagem via `/api/overview`, config via `GET/PUT /api/sla`
- **Relatório executivo** (`GET /report`): HTML standalone printável (→ PDF pelo browser) com KPIs, OWASP Top 10, top 10 risk scores, SLA e último scan por projeto; botão "Relatório" no Dashboard
- **Risk score por projeto**: Σ peso da severidade (critical 10, high 5, medium 2, low 0.5) × fator de idade (até 3× aos 60+ dias) × 1.5 se repo público; badge colorido na aba Projects (coluna nova, ordenável) e card "Projetos mais arriscados" agora rankeia por score
- **Scan diff-only em PR**: Semgrep roda com `--baseline-commit` (base do PR) — reporta só findings novos; fallback para scan completo se o baseline falhar; `fetch-depth: 0`
- **Notificações Slack / Discord / WhatsApp (CallMeBot)**: disparadas em background quando findings critical NOVOS chegam no ingest; sub-aba Settings→Notificações com as 3 URLs + botão "Testar canais"; endpoints `GET/PUT /api/notify/config`, `POST /api/notify/test`

## [0.13.1] - 2026-08-30

### Fixed
- **"Invalid secret, SNYK_TOKEN is not defined"**: caller de repo com template antigo (Snyk) passava `SNYK_TOKEN` explicitamente e o GitHub invalidava o workflow. O reusable agora declara `SNYK_TOKEN` (ignorado) e `SECPIPE_TOKEN` como secrets opcionais em `workflow_call` — callers legados voltam a validar sem mudanças

## [0.13.0] - 2026-08-30

### Added — paridade Checkmarx: 3 engines novos + PR decoration + compliance
- **DAST (OWASP ZAP)**: novo job `dast-zap` — baseline scan contra a app rodando; opt-in por repo via input `dast_url`; conversor `scripts/zap2sarif.py` (risk→severity, CWE das tags)
- **API Security (Spectral)**: job `api-security` detecta specs OpenAPI/Swagger no repo e linta com as regras OAS do Spectral; conversor `scripts/spectral2sarif.py`; neutro quando não há spec
- **Supply Chain (OSSF Scorecard)**: job `supply-chain` roda o Scorecard (SARIF nativo — pinned deps, token permissions, branch protection, dangerous workflows); só em push (limitação do Scorecard em PR)
- **PR decoration**: `scripts/pr_comment.py` posta/atualiza comentário no PR com veredito do gate, tabela por severidade, top 10 critical/high com OWASP e saída do gate em caso de reprovação (upsert por marker, 403 tolerado)
- **Compliance OWASP Top 10**: `normalize.py` extrai CWE/OWASP das tags SARIF + mapa CWE→OWASP 2021 (~150 CWEs); Trivy CVEs caem em A06; findings ganham campos `cwe`/`owasp` (colunas novas no SQLite); card **"OWASP Top 10"** no dashboard customizável; CWE/OWASP visíveis no painel de detalhes

### Changed
- Gate agora roda com `if: !cancelled()` e needs de todos os 6 jobs — agrega o que chegou mesmo com jobs opcionais pulados; verdito preservado via `continue-on-error` + passo final `Enforce gate`
- `example-caller.yml` documenta `permissions: pull-requests: write` (necessário no caller para o PR decoration) e o input `dast_url`

## [0.12.3] - 2026-08-30

### Changed
- **Gate default: `high` → `critical`** (input `fail_on` e fallback do workflow reutilizável): 9 repos reprovavam todo scan por terem ≥1 high aberto (~130 highs de backlog). Highs continuam reportados no dashboard e contam na política, mas só critical bloqueia o merge até o backlog ser zerado via AI autofix — aí o default pode voltar a `high`
- Repos que passam `fail_on: high` explicitamente no caller continuam estritos (o input sobrepõe o default)

## [0.12.2] - 2026-08-30

### Changed — Lucide icons em toda a aplicação (sem emojis em controles)
- Botões de IA: sparkles (sugerir/diagnosticar), wrench (aplicar PR), rotate-cw (verificar), loader-circle nos estados de progresso
- Dashboard: sliders-horizontal (Personalizar), check (Concluir), chevron-left/right (mover card), minus/plus (largura), x (remover)
- Tabelas: chevron-right no expand do Results, chevron-down/up no Detalhes do Findings e no Hide do Why?
- CSV: ícone download nos 4 botões de exportação
- Badges de visibilidade: lock/globe no lugar de 🔒/🌐
- Títulos de card: bot (AI Engine), shield-check (2FA e passo TOTP do login)
- Modais: wrench/rotate-cw/trash-2 nos títulos, com `createIcons()` ao abrir
- Novo helper `btnLabel(btn, icon, label)` para mutar labels de botão preservando o ícone; `createIcons()` garantido após todos os renders dinâmicos (painel de finding incluído)

## [0.12.1] - 2026-08-30

### Added — verificação de correção com re-scan automático
- **"🔁 Verificar (novo scan)"** no painel do finding: dispara o `security.yml` no repo, aguarda o pipeline rodar e o resultado ser ingerido (até 15 min, polling a cada 20s) e confere o desfecho:
  - Finding sumiu do novo scan → já foi **marcado como Fixed** pelo ingest automático → mensagem verde ✅ e tabelas atualizadas
  - Finding ainda presente → aviso vermelho ❌ (correção não foi efetiva / PR não mergeado)
- Endpoint `POST /api/ai/verify` (job assíncrono como as demais operações de IA)
- Dispatch do workflow extraído para helper `_dispatch_security_scan()` reutilizado pelo botão ▶ Scan e pela verificação
- Timeout do polling de jobs de IA: 10 → 20 min

## [0.12.0] - 2026-08-30

### Added
- **Settings → sub-aba "AI"**: seletor de modelo Claude para o AI Engine — Sonnet 4.6, Sonnet 5, Opus 5, Fable 5, Haiku 4.5, ou ID customizado digitado
- Modelo escolhido persiste no SQLite (tabela `settings`) e vale imediatamente para as três operações (sugerir, aplicar PR, diagnosticar) — sem restart
- Endpoints `GET /api/ai/config` (viewer) e `PUT /api/ai/config` (admin, com validação do ID)
- Sub-aba mostra status do engine (ativo via Claude Code/API key) e o modelo em uso
- Settings reorganizado em sub-abas **Policy** | **AI**

## [0.11.1] - 2026-08-30

### Fixed
- **502 no autofix**: o quick tunnel do Cloudflare derruba requests após ~100s e o autofix leva 1–3 min. As três operações de IA agora são **assíncronas**: o POST retorna um job id na hora e o front faz polling em `GET /api/ai/jobs/{id}` a cada 4s, com contador de tempo ao vivo ("⏳ 45s — Claude está trabalhando…") e timeout de 10 min
- Jobs guardados em memória com expiração de 1h

### Changed
- **Modais próprios no lugar de alert()/confirm() nativos**: `uiConfirm`/`uiAlert` com glassmorphism, Esc/Enter/clique-fora, botão vermelho para ações destrutivas
- Confirmação do autofix virou modal com repo/arquivo formatados
- `delRepo` agora pede confirmação (antes deletava o projeto sem perguntar nada)

## [0.11.0] - 2026-08-30

### Added — Autofix: a IA aplica a correção e abre PR
- **"🔧 Aplicar correção (abre PR)"** no painel de cada finding: o backend clona o repo (depth 1) em tmpfs, roda o **Claude Code em modo agente** (tools Edit/Write/Read/Grep/Glob, até 25 turns) para corrigir o código de verdade, commita num branch `secpipe/ai-fix-<ts>`, faz push e **abre um Pull Request** via API do GitHub
- Nada vai direto para a main: o PR traz tabela do finding, resumo do que a IA mudou e aviso de revisão; o scan de segurança roda automaticamente no PR
- UI mostra link do PR, resumo e o **diff completo** expansível; confirm() antes de executar
- Endpoint `POST /api/ai/autofix` (role analyst); workdir temporário sempre limpo (`finally`); token do GitHub mascarado em qualquer erro de git
- Se a IA não alterar nada, retorna a análise sem criar branch/PR
- Dockerfile: adicionado `git` à imagem

## [0.10.1] - 2026-08-30

### Changed
- **AI Engine via Claude Code**: agora funciona com a autenticação da conta Claude (Pro/Max) sem precisar de API key — gere o token com `claude setup-token` e coloque `CLAUDE_CODE_OAUTH_TOKEN` no `.env`
- Backend roteia automaticamente: `ANTHROPIC_API_KEY` → API direta; senão `CLAUDE_CODE_OAUTH_TOKEN` → Claude Code CLI headless (`claude -p`) dentro do container
- Dockerfile instala Node.js + `@anthropic-ai/claude-code`; CLI roda com `HOME=/tmp` (tmpfs) por causa do filesystem read-only
- Compose: tmpfs 64m→256m (sem `noexec`), mem_limit 256m→768m, cpus 1.0 — folga para o CLI Node
- `GET /api/ai/status` agora informa `via`: `api` ou `claude-code`

## [0.10.0] - 2026-08-30

### Added — AI Engine (Claude no backend)
- **"✨ Sugerir correção com IA"** no painel de detalhes de cada finding (Results): o backend busca o código real no GitHub (±25 linhas ao redor da linha do finding) e pede ao Claude causa, código corrigido e riscos relacionados — resposta em markdown renderizada inline
- **"✨ Diagnosticar com IA"** no painel Why? dos scans que falharam (Scans): o backend lê os jobs do run + as últimas 150 linhas do log do job que falhou e o Claude explica a causa provável, como corrigir e se é bloqueio do gate ou erro técnico
- Endpoints: `POST /api/ai/fix`, `POST /api/ai/diagnose`, `GET /api/ai/status` (requerem role analyst; viewer só consulta status)
- Cache em memória por finding/run — reanalisar não gasta tokens repetidos
- Config: `ANTHROPIC_API_KEY` no `.env` (novo no `.env.example`); modelo via `SECPIPE_AI_MODEL` (padrão `claude-sonnet-5`)
- Chamada à API Anthropic via stdlib `urllib` (zero dependências novas)

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
