#Requires -Version 5.1
param(
    [string]$Root = $(Join-Path $env:LOCALAPPDATA 'github-sponsorship-production'),
    [string]$Python,
    [switch]$NoBrowser,
    [switch]$Demo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$scriptsDir = $PSScriptRoot
$project = [IO.Path]::GetFullPath((Join-Path $scriptsDir '..'))
$loginUrl = 'http://127.0.0.1:8765/#team-login'
$healthUrl = 'http://127.0.0.1:8765/healthz'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host ('>>> ' + $Message) -ForegroundColor Cyan
}

function Resolve-Python([string]$Preferred) {
    if ($Preferred) {
        if (-not (Test-Path -LiteralPath $Preferred)) {
            throw ('Python not found: ' + $Preferred)
        }
        return (Get-Item -LiteralPath $Preferred).FullName
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $resolved = & py -3 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) { return ($resolved | Select-Object -Last 1).Trim() }
    }
    throw 'Python 3.11+ not found. Install Python and add it to PATH.'
}

function Ensure-Package([string]$PythonPath) {
    Push-Location -LiteralPath $project
    try {
        $probe = 'import worker; print(getattr(worker, "__version__", "") or "ok")'
        $check = & $PythonPath -I -c $probe 2>$null
        $ver = if ($check) { ($check | Select-Object -Last 1).Trim() } else { '' }
        if ($LASTEXITCODE -eq 0 -and $ver) {
            Write-Host ('Package ready: worker ' + $ver)
            return
        }
    } catch {
    } finally {
        Pop-Location
    }

    Write-Step 'Installing package (pip install -e .)'
    Push-Location -LiteralPath $project
    try {
        & $PythonPath -m pip install -e . --quiet
        if ($LASTEXITCODE -ne 0) { throw 'pip install -e . failed' }
    } finally {
        Pop-Location
    }
}

function Test-AlreadyHealthy {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 -MaximumRedirection 0
        return ($health.ok -eq $true)
    } catch {
        return $false
    }
}

Write-Host '========================================' -ForegroundColor Green
Write-Host '  OpenDev Elite One-Click Start' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ('Project:  ' + $project)
Write-Host ('Data:     ' + $Root)

$pythonPath = Resolve-Python $Python
Write-Host ('Python:   ' + $pythonPath)

if (Test-AlreadyHealthy) {
    Write-Step 'Service already running'
    $result = [ordered]@{
        status = 'already_running'
        url    = 'http://127.0.0.1:8765'
        root   = $Root
    }
} else {
    Ensure-Package $pythonPath

    Write-Step 'Running setup-local'
    Push-Location -LiteralPath $project
    try {
        $setupArgs = @('-I', '-X', 'utf8', 'main.py', 'setup-local', '--root', $Root)
        if ($Demo) { $setupArgs += '--demo' }
        $setupOut = & $pythonPath @setupArgs
        if ($LASTEXITCODE -ne 0) { throw 'setup-local failed' }
        $setupJson = ($setupOut -join "`n") | ConvertFrom-Json
        if ($setupJson.root) { $Root = [string]$setupJson.root }
        if ($setupJson.url) { $loginUrl = ([string]$setupJson.url).TrimEnd('/') + '/#team-login' }
    } finally {
        Pop-Location
    }

    Write-Step 'Starting serve + run'
    $startArgs = @{
        Root   = $Root
        Python = $pythonPath
    }
    if ($Demo) { $startArgs['Demo'] = $true }
    $startText = & (Join-Path $scriptsDir 'start-local.ps1') @startArgs
    $result = ($startText | Out-String) | ConvertFrom-Json
    if (-not $result -or -not $result.status) { throw 'start-local.ps1 returned unexpected output' }
    if ($result.url) {
        $loginUrl = ([string]$result.url).TrimEnd('/') + '/#team-login'
    }
}

Write-Host ''
Write-Host 'OK - service is up' -ForegroundColor Green
Write-Host ('URL:      ' + $loginUrl)
Write-Host 'Login:    OpenDev / 123456'
Write-Host ('Data root:' + $result.root)
Write-Host 'Stop with: 一键停止.bat'

if (-not $NoBrowser) {
    Write-Step 'Opening browser'
    Start-Process $loginUrl
}

$result | ConvertTo-Json