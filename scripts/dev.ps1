# 一键启动开发环境（PowerShell）
# 用法: .\scripts\dev.ps1 [-SkipCompose]
param(
    [switch]$SkipCompose
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:DATABASE_URL = "postgresql+asyncpg://oiuser:oipass@localhost:5432/oidiag"

function Wait-For([string]$Name, [scriptblock]$Check, [int]$TimeoutSec = 90) {
    Write-Host "等待 $Name ..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Check) { Write-Host "  $Name 就绪" -ForegroundColor Green; return $true }
        Start-Sleep -Seconds 2
    }
    Write-Host "  $Name 超时（$TimeoutSec s）" -ForegroundColor Yellow
    return $false
}

if (-not $SkipCompose) {
    docker compose -f deploy/compose.dev.yml up -d
    Wait-For "postgres" { docker exec oi-postgres pg_isready -U oiuser -d oidiag 2>$null | Out-Null; $LASTEXITCODE -eq 0 } | Out-Null
    Wait-For "redis" { (docker exec oi-redis redis-cli ping 2>$null) -match "PONG" } | Out-Null
    Wait-For "judge" { (Invoke-WebRequest -Uri http://localhost:5050/version -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } | Out-Null
}

if (-not (Test-Path "backend\.venv")) {
    Write-Host "创建虚拟环境 ..." -ForegroundColor Cyan
    python -m venv backend\.venv
    & backend\.venv\Scripts\python.exe -m pip install -q -e "backend[dev]"
}

Write-Host "执行迁移 ..." -ForegroundColor Cyan
Push-Location backend
& .\.venv\Scripts\alembic.exe upgrade head
Pop-Location

Write-Host "启动 API: http://127.0.0.1:8000/docs" -ForegroundColor Green
Push-Location backend
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
Pop-Location
