Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not ('SponsorshipNativePath' -as [type])) {
    Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class SponsorshipNativePath {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint size, uint flags);
    public static string Resolve(string path) {
        using (var handle = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) return path;
            var result = new StringBuilder(32768);
            var count = GetFinalPathNameByHandle(handle, result, 32768, 0);
            if (count == 0 || count >= 32768) throw new InvalidOperationException("Cannot resolve deployment directory.");
            var value = result.ToString();
            return value.StartsWith(@"\\?\") ? value.Substring(4) : value;
        }
    }
}
'@
}

function Get-LocalRoot([string]$Root) {
    if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA 'github-sponsorship' }
    $full = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($full.StartsWith('\\') -or $full -match '(?i)(^|[\\/])OneDrive[^\\/]*([\\/]|$)') {
        throw 'Use a local deployment directory outside OneDrive and UNC shares.'
    }
    return [SponsorshipNativePath]::Resolve($full)
}

function Enter-LocalLock([string]$Root) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = [Threading.Mutex]::new($false, ('Local\Sponsorship-' + $digest))
    try { $acquired = $mutex.WaitOne(15000) }
    catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { $mutex.Dispose(); throw 'Another start/stop operation holds the deployment lock.' }
    return $mutex
}

function Get-OwnedProcess($Record, [string]$Root, [string]$Kind) {
    if ($Record.root -cne $Root -or $Record.kind -cne $Kind -or $Record.instance -notmatch '^[a-f0-9]{32}$') {
        throw ('PID record does not identify this deployment; no process was stopped. Root match=' + ($Record.root -ceq $Root) + ', role match=' + ($Record.kind -ceq $Kind) + ', instance valid=' + ($Record.instance -match '^[a-f0-9]{32}$'))
    }
    $info = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$Record.pid)
    if (-not $info) { return $null }
    $marker = '--instance ' + $Record.instance
    if ($info.ExecutablePath -ine $Record.python -or $info.CommandLine -cne $Record.command_line -or
        -not $info.CommandLine.Contains('-m worker.deployment process') -or
        -not $info.CommandLine.Contains('--root "' + $Root + '"') -or
        -not $info.CommandLine.Contains('--kind ' + $Kind) -or -not $info.CommandLine.Contains($marker)) {
        throw 'PID has been reused or command identity differs; refusing to manage it.'
    }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -cne [string]$Record.creation_ticks) {
        throw 'Process creation time differs from its PID record; refusing to manage it.'
    }
    return $process
}

function Read-ProcessRecord([string]$Path) {
    if (Test-Path -LiteralPath $Path) { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    return $null
}

function Get-OwnedWorkload($Record, [string]$Root, [string]$Kind) {
    $parent = Get-OwnedProcess -Record $Record -Root $Root -Kind $Kind
    if ($parent) { $parent }
    # Windows virtualenv launchers spawn a base-interpreter child. Its exact
    # command line and unique instance must still match this deployment.
    $children = @(Get-CimInstance Win32_Process -Filter ('ParentProcessId=' + [int]$Record.pid))
    foreach ($child in $children) {
        if ($child.CommandLine -cne $Record.command_line -or
            [IO.Path]::GetFileName($child.ExecutablePath) -notmatch '^python(?:w|[0-9.]*)?\.exe$') { continue }
        $process = Get-Process -Id ([int]$child.ProcessId) -ErrorAction SilentlyContinue
        if (-not $process -or $process.StartTime.ToUniversalTime().Ticks -lt [long]$Record.creation_ticks) { continue }
        $childRecord = [pscustomobject]@{ pid = $child.ProcessId; root = $Record.root; kind = $Record.kind;
            instance = $Record.instance; python = $child.ExecutablePath; command_line = $child.CommandLine;
            creation_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString() }
        Get-OwnedProcess -Record $childRecord -Root $Root -Kind $Kind
    }
}

function Stop-OwnedWorkload($Record, [string]$Root, [string]$Kind) {
    $processes = @(Get-OwnedWorkload -Record $Record -Root $Root -Kind $Kind)
    # Child first, then launcher. Never use taskkill /T on an unchecked PID.
    foreach ($process in @($processes | Sort-Object @{Expression={ $_.Id -eq [int]$Record.pid }})) {
        Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
        $process.WaitForExit(10000) | Out-Null
    }
    return $processes.Count
}

function Write-ProcessRecord([string]$Path, $Record) {
    $temporary = $Path + '.tmp'
    $Record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
