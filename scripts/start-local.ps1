param([string]$Root, [string]$Python = 'python', [switch]$Demo)
. (Join-Path $PSScriptRoot 'local-runtime.ps1')
$Root = Get-LocalRoot $Root
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$mutex = $null
$created = [Collections.Generic.List[object]]::new()
try {
    Push-Location -LiteralPath $project
    try {
        $setupArguments = @('-I', '-X', 'utf8', '-m', 'worker.deployment', 'setup', '--root', $Root)
        if ($Demo) { $setupArguments += '--demo' }
        $setupText = & $pythonPath @setupArguments
        if ($LASTEXITCODE -ne 0) { throw 'Local setup failed; existing files were preserved.' }
        $setup = ($setupText -join "`n") | ConvertFrom-Json
        $Root = [string]$setup.root
        $publicUrl = [string]$setup.url
        $healthHost = [string]$setup.health_host
        $mutex = Enter-LocalLock $Root
        foreach ($kind in @('serve', 'run')) {
            $recordPath = Join-Path $Root ('runtime\' + $kind + '.json')
            $record = Read-ProcessRecord $recordPath
            if ($record) {
                $existing = @(Get-OwnedWorkload -Record $record -Root $Root -Kind $kind)
                if ($existing.Count) {
                    if ([bool]$record.demo -ne [bool]$Demo) { throw 'Existing process uses a different demo mode; stop it before switching.' }
                    continue
                }
                Remove-Item -LiteralPath $recordPath
            }
            # Do not create duplicates if a previous launcher lost its PID record.
            $untracked = @(Get-CimInstance Win32_Process | Where-Object {
                $_.CommandLine -and $_.CommandLine.Contains('-m worker.deployment process') -and
                $_.CommandLine.Contains('--root "' + $Root + '"') -and $_.CommandLine.Contains('--kind ' + $kind)
            })
            if ($untracked.Count) { throw 'An untracked process already uses this deployment. Inspect it before starting again.' }
            $instance = [Guid]::NewGuid().ToString('N')
            $arguments = @('-I', '-X', 'utf8', '-u', '-m', 'worker.deployment', 'process', '--root', ('"' + $Root + '"'), '--kind', $kind, '--instance', $instance)
            if ($Demo) { $arguments += '--demo' }
            $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $Root ('logs\' + $kind + '.bootstrap.out.log')) `
                -RedirectStandardError (Join-Path $Root ('logs\' + $kind + '.bootstrap.err.log'))
            $info = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $process.Id)
            if (-not $info) { throw ('The ' + $kind + ' process exited during startup; inspect its local bootstrap log.') }
            $record = [ordered]@{ pid = $process.Id; root = $Root; kind = $kind; instance = $instance;
                python = $info.ExecutablePath; command_line = $info.CommandLine;
                creation_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString(); demo = [bool]$Demo }
            Write-ProcessRecord $recordPath $record
            $created.Add([pscustomobject]@{ record = [pscustomobject]$record; path = $recordPath; kind = $kind })
        }
        $healthy = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            foreach ($kind in @('serve', 'run')) {
                $record = Read-ProcessRecord (Join-Path $Root ('runtime\' + $kind + '.json'))
                if (-not @(Get-OwnedWorkload -Record $record -Root $Root -Kind $kind).Count) { throw ('The ' + $kind + ' process stopped during health checking.') }
            }
            try {
                $serverRecord = Read-ProcessRecord (Join-Path $Root 'runtime\serve.json')
                $serverPids = @(Get-OwnedWorkload -Record $serverRecord -Root $Root -Kind 'serve' | Select-Object -ExpandProperty Id)
                $listenerPids = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
                if (-not @($listenerPids | Where-Object { $_ -in $serverPids }).Count) { throw 'The listener does not belong to this deployment.' }
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/healthz' -Headers @{ Host = $healthHost } -TimeoutSec 1 -MaximumRedirection 0
                if ($health.ok -eq $true) { $healthy = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 250
        }
        if (-not $healthy) { throw 'Startup health check failed; inspect the local service log.' }
        [ordered]@{ status = 'running'; url = $publicUrl; root = $Root; synthetic_demo = [bool]$Demo;
            credentials_directory = (Join-Path $Root 'credentials'); logs_directory = (Join-Path $Root 'logs') } | ConvertTo-Json
    } finally { Pop-Location }
} catch {
    foreach ($item in $created) {
        try {
            Stop-OwnedWorkload -Record $item.record -Root $Root -Kind $item.kind | Out-Null
            Remove-Item -LiteralPath $item.path -ErrorAction SilentlyContinue
        } catch { Write-Warning 'A newly started process needs manual inspection; its PID record was retained.' }
    }
    throw
} finally { if ($mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() } }
