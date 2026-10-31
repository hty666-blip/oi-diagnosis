# 一键启动开发环境（PowerShell）
# 用法: .\scripts\dev.ps1 [-SkipCompose] [-SkipJudge]
param(
    [switch]$SkipCompose,
    [switch]$SkipJudge
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = "1"

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
    $services = @("mysql", "redis")
    if (-not $SkipJudge) { $services += "judge" }
    docker compose --env-file .env -f deploy/compose.dev.yml up -d @services
    Wait-For "mysql" { (docker inspect --format '{{.State.Health.Status}}' oi-mysql 2>$null) -eq "healthy" } | Out-Null
    Wait-For "redis" { (docker exec oi-redis redis-cli ping 2>$null) -match "PONG" } | Out-Null
    if (-not $SkipJudge) {
        Wait-For "judge" { (Invoke-WebRequest -Uri http://localhost:5050/version -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } | Out-Null
    }
}

if (-not (Test-Path "backend\.venv")) {
    Write-Host "创建虚拟环境 ..." -ForegroundColor Cyan
    python -m venv backend\.venv
    & backend\.venv\Scripts\python.exe -m pip install -q -e "backend[dev]"
}

Write-Host "初始化数据库 ..." -ForegroundColor Cyan
& backend\.venv\Scripts\python.exe scripts\init_database.py
if ($LASTEXITCODE -ne 0) { throw "数据库初始化失败" }

Write-Host "执行迁移 ..." -ForegroundColor Cyan
Push-Location backend
& .\.venv\Scripts\alembic.exe upgrade head
Pop-Location

Write-Host "启动 API: http://127.0.0.1:8000/docs" -ForegroundColor Green
Push-Location backend
# Windows 下 Uvicorn 的 --reload 会使用 SelectorEventLoop，无法启动 Docker 判题子进程。
# 保持单进程运行；修改后端代码后手动重启此脚本即可。
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
Pop-Location
