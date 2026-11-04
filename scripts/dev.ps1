# 一键启动本地开发环境（PowerShell）；可先用 -CheckOnly 检查参数。
param(
    [switch]$SkipCompose,
    [switch]$SkipJudge,
    [switch]$GoJudge,
    [switch]$SkipInstall,
    [switch]$DisableLlm,
    [switch]$NoReload,
    [switch]$Reload,
    [switch]$CheckOnly,
    [ValidateRange(1024, 65535)][int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:PYTHONUTF8 = '1'
if ($DisableLlm) { $env:LLM_ENABLED = 'false' }
$BackendPath = Join-Path $ProjectRoot 'backend'
$PythonPath = Join-Path $BackendPath '.venv\Scripts\python.exe'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step 失败（退出码 $LASTEXITCODE），请处理后重新启动。" }
}

function Wait-For([string]$Name, [scriptblock]$Check, [int]$TimeoutSec = 90) {
    Write-Host "等待 $Name ..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            if (& $Check) { Write-Host "  $Name 就绪" -ForegroundColor Green; return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "$Name 在 $TimeoutSec 秒内未就绪，已停止启动，请检查容器状态。"
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Get-Command python -ErrorAction Stop | Out-Null
    if (Test-Path -LiteralPath (Join-Path $BackendPath '.venv')) {
        throw '现有 backend\.venv 缺少 Windows Python，请检查环境，不会自动覆盖。'
    }
}
if (-not $SkipCompose) { Get-Command docker -ErrorAction Stop | Out-Null }
if ($CheckOnly) {
    Write-Host "启动参数检查通过：API 端口 $Port；未安装依赖或启动服务。"
    exit 0
}

if (-not $SkipCompose) {
    $ComposeArgs = @('compose')
    $EnvironmentPath = Join-Path $ProjectRoot '.env'
    if (Test-Path -LiteralPath $EnvironmentPath) { $ComposeArgs += @('--env-file', $EnvironmentPath) }
    $ComposeArgs += @('-f', (Join-Path $ProjectRoot 'deploy\compose.dev.yml'))
    $Services = @('mysql', 'redis')
    $useGoJudge = $GoJudge -or $env:JUDGE_BACKEND -eq 'gojudge'
    if (-not $env:JUDGE_BACKEND -and (Test-Path -LiteralPath $EnvironmentPath)) {
        $useGoJudge = $useGoJudge -or [bool](Select-String -LiteralPath $EnvironmentPath -Pattern '^\s*JUDGE_BACKEND\s*=\s*["'']?gojudge["'']?\s*$' -Quiet)
    }
    if (-not $SkipJudge -and $useGoJudge) {
        $ComposeArgs += @('--profile', 'gojudge')
        $Services += 'judge'
    } elseif (-not $SkipJudge) {
        & docker image inspect oi-judge:dev --format '{{.Id}}' *> $null
        if ($LASTEXITCODE -ne 0) {
            & docker @ComposeArgs build judge
            Assert-NativeSuccess 'Docker 判题镜像构建'
        }
    }
    & docker @ComposeArgs up -d @Services
    Assert-NativeSuccess '开发基础设施启动'
    foreach ($serviceName in @('mysql', 'redis')) {
        Wait-For $serviceName {
            $containerId = & docker @ComposeArgs ps -q $serviceName
            if ($LASTEXITCODE -ne 0 -or -not $containerId) { return $false }
            $health = & docker inspect --format '{{.State.Health.Status}}' $containerId 2>$null
            return $LASTEXITCODE -eq 0 -and $health -eq 'healthy'
        }
    }
    if (-not $SkipJudge -and $useGoJudge) {
      Wait-For 'judge' {
        $containerId = & docker @ComposeArgs ps -q judge
        if ($LASTEXITCODE -ne 0 -or -not $containerId) { return $false }
        $running = & docker inspect --format '{{.State.Running}}' $containerId 2>$null
        return $LASTEXITCODE -eq 0 -and $running -eq 'true'
      }
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    & python -m venv (Join-Path $BackendPath '.venv')
    Assert-NativeSuccess 'Python 环境创建'
}

Push-Location $BackendPath
try {
    if (-not $SkipInstall) {
        & $PythonPath -m pip install -e '.[dev]'
        Assert-NativeSuccess '后端依赖安装'
    }
    & $PythonPath -c "import sys; from app.core.config import settings; sys.exit(0 if settings.app_env.strip().lower() == 'dev' else 2)"
    Assert-NativeSuccess '开发环境检查：该脚本仅允许 APP_ENV=dev'
    & $PythonPath (Join-Path $ProjectRoot 'scripts\init_database.py')
    Assert-NativeSuccess 'MySQL 数据库初始化'
    & $PythonPath -m alembic upgrade head
    Assert-NativeSuccess '数据库迁移'
    Write-Host "启动 API: http://127.0.0.1:$Port/docs" -ForegroundColor Green
    $ServerArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port")
    # Windows Docker 子进程需要默认事件循环，重载仅在明确指定时启用。
    if ($Reload -and -not $NoReload) { $ServerArgs += '--reload' }
    & $PythonPath @ServerArgs
    Assert-NativeSuccess 'API 服务'
} finally { Pop-Location }
