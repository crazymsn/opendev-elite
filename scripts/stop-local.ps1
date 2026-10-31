param([string]$Root)
. (Join-Path $PSScriptRoot 'local-runtime.ps1')
$Root = Get-LocalRoot $Root
$mutex = Enter-LocalLock $Root
try {
    $stopped = @()
    foreach ($kind in @('run', 'serve')) {
        $recordPath = Join-Path $Root ('runtime\' + $kind + '.json')
        $record = Read-ProcessRecord $recordPath
        if (-not $record) { continue }
        $processCount = Stop-OwnedWorkload -Record $record -Root $Root -Kind $kind
        if ($processCount) {
            $stopped += $kind
        }
        Remove-Item -LiteralPath $recordPath
    }
    [ordered]@{ status = 'stopped'; root = $Root; stopped_roles = $stopped; data_preserved = $true } | ConvertTo-Json
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
