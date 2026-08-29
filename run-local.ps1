<#
.SYNOPSIS
  Roda o pipeline de segurança localmente via Docker: Semgrep + Trivy + Gitleaks,
  depois normaliza os SARIFs e aplica o gate.

.EXAMPLE
  ./run-local.ps1 -Target C:\repos\minha-api
  ./run-local.ps1 -Target C:\repos\minha-api -FailOn critical
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Target,

    [ValidateSet("critical", "high", "medium", "low")]
    [string]$FailOn = "high"
)

$ErrorActionPreference = "Stop"
$Target = (Resolve-Path $Target).Path
$here = $PSScriptRoot
$out = Join-Path $here "out"
New-Item -ItemType Directory -Force $out | Out-Null

Write-Host "`n=== [1/3] SAST: Semgrep ===" -ForegroundColor Cyan
docker run --rm -v "${Target}:/src" -v "${out}:/out" semgrep/semgrep `
    semgrep scan --config auto --sarif --output /out/semgrep.sarif /src
if ($LASTEXITCODE -gt 1) { Write-Warning "Semgrep terminou com erro ($LASTEXITCODE)" }

Write-Host "`n=== [2/3] SCA + IaC + Secrets: Trivy ===" -ForegroundColor Cyan
docker run --rm -v "${Target}:/src" -v "${out}:/out" -v "${here}\configs:/cfg" aquasec/trivy `
    fs /src --config /cfg/trivy.yaml --format sarif --output /out/trivy.sarif --exit-code 0

Write-Host "`n=== [3/3] Secrets: Gitleaks ===" -ForegroundColor Cyan
# Com repositório git escaneia o histórico de commits; sem, escaneia os arquivos.
$gitleaksArgs = if (Test-Path (Join-Path $Target ".git")) {
    @("detect", "--source", "/src")
} else {
    @("dir", "/src")
}
docker run --rm -v "${Target}:/src" -v "${out}:/out" -v "${here}\configs:/cfg" zricethezav/gitleaks:latest `
    @gitleaksArgs --config /cfg/gitleaks.toml `
    --report-format sarif --report-path /out/gitleaks.sarif --exit-code 0

Write-Host "`n=== Normalizando e aplicando gate ===" -ForegroundColor Cyan
python (Join-Path $here "scripts\normalize.py") $out --output (Join-Path $out "findings.json")
python (Join-Path $here "scripts\gate.py") (Join-Path $out "findings.json") `
    --policy (Join-Path $here "policy\policy.yml") --fail-on $FailOn

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nResultado completo em $out\findings.json" -ForegroundColor Yellow
    exit 1
}
Write-Host "`nResultado completo em $out\findings.json" -ForegroundColor Green
