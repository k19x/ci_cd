<#
.SYNOPSIS
  Integra TODOS os repositórios da conta k19x ao SecPipe:
  1. Cadastra cada um no dashboard local (http://localhost:8200)
  2. Cria .github/workflows/security.yml em cada repo via API do GitHub
     (pula os que já têm o arquivo; exclui o próprio ci_cd)

.EXAMPLE
  .\integrate-all.ps1              # tudo
  .\integrate-all.ps1 -DryRun     # só mostra o que faria
#>
param([switch]$DryRun)

$ErrorActionPreference = "Continue"
$yaml = @'
name: security
on:
  pull_request:
  push:
    branches: [main, master]
permissions:
  contents: read
  security-events: write
jobs:
  scan:
    uses: k19x/ci_cd/.github/workflows/security-scan.yml@main
    with:
      fail_on: high
    secrets: inherit
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($yaml))
$path = ".github/workflows/security.yml"
$msg = "ci: integra SecPipe security scan"

$repos = gh repo list k19x --limit 100 --json name,isArchived --jq '.[] | select(.isArchived==false) | .name' |
    Where-Object { $_ -ne "ci_cd" }

$ok = @(); $skipped = @(); $failed = @()
foreach ($r in $repos) {
    Write-Host "-> k19x/$r" -ForegroundColor Cyan

    if (-not $DryRun) {
        try {
            Invoke-RestMethod -Method Post http://localhost:8200/api/repos -ContentType application/json `
                -Body (@{ name = "k19x/$r"; url = "https://github.com/k19x/$r" } | ConvertTo-Json) -TimeoutSec 5 | Out-Null
        } catch {} # 409 = já cadastrado
    }

    gh api "repos/k19x/$r/contents/$path" --jq .sha 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $skipped += $r; continue }

    if ($DryRun) { $ok += $r; continue }
    $result = gh api -X PUT "repos/k19x/$r/contents/$path" -f message=$msg -f content=$b64 2>&1
    if ($LASTEXITCODE -eq 0) { $ok += $r } else { $failed += "$r : $($result | Select-Object -Last 1)" }
}

""
"=== RESUMO $(if ($DryRun) { '(DRY RUN - nada foi alterado)' }) ==="
"Workflow adicionado ($($ok.Count)): $($ok -join ', ')"
"Já tinham workflow ($($skipped.Count)): $($skipped -join ', ')"
if ($failed.Count) { "Falharam ($($failed.Count)):"; $failed | ForEach-Object { "  $_" } }
