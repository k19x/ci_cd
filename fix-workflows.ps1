<#
.SYNOPSIS
  Atualiza o security.yml de todos os repos k19x com o formato correto:
  - permissions no nível do workflow
  - workflow_dispatch trigger
  - with: fail_on: high
#>
param([switch]$DryRun)

$ErrorActionPreference = "Continue"
$yaml = @'
name: security
on:
  pull_request:
  push:
    branches: [main, master]
  workflow_dispatch:
permissions:
  contents: read
  security-events: write
  actions: read
jobs:
  scan:
    uses: k19x/ci_cd/.github/workflows/security-scan.yml@main
    with:
      fail_on: high
    secrets: inherit
'@

$b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($yaml))
$path = ".github/workflows/security.yml"
$msg  = "ci: fix permissions + add workflow_dispatch"

$repos = gh repo list k19x --limit 100 --json name,isArchived `
    --jq '.[] | select(.isArchived==false) | .name' |
    Where-Object { $_ -ne "ci_cd" }

$ok=@(); $skipped=@(); $failed=@()
foreach ($r in $repos) {
    Write-Host "-> k19x/$r" -ForegroundColor Cyan

    $sha = gh api "repos/k19x/$r/contents/$path" --jq .sha 2>$null
    if (-not $sha) { $skipped += $r; Write-Host "   sem workflow, pulando" -ForegroundColor Yellow; continue }

    if ($DryRun) { $ok += $r; continue }

    $result = gh api -X PUT "repos/k19x/$r/contents/$path" `
        -f message=$msg -f content=$b64 -f sha=$sha 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ok += $r
        Write-Host "   atualizado" -ForegroundColor Green
    } else {
        $failed += "$r : $($result | Select-Object -Last 1)"
        Write-Host "   FALHOU" -ForegroundColor Red
    }
    Start-Sleep -Milliseconds 300
}

""
"=== RESUMO $(if ($DryRun){'(DRY RUN)'})==="
"Atualizados ($($ok.Count)): $($ok -join ', ')"
"Sem workflow ($($skipped.Count)): $($skipped -join ', ')"
if ($failed.Count) { "Falharam ($($failed.Count)):"; $failed | ForEach-Object { "  $_" } }
