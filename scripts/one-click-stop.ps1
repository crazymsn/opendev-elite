#Requires -Version 5.1
param(
    [string]$Root = $(Join-Path $env:LOCALAPPDATA 'github-sponsorship-production')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

Write-Host '========================================' -ForegroundColor Yellow
Write-Host '  OpenDev Elite One-Click Stop' -ForegroundColor Yellow
Write-Host '========================================' -ForegroundColor Yellow
Write-Host ('Data: ' + $Root)

$resultText = & (Join-Path $PSScriptRoot 'stop-local.ps1') -Root $Root
$result = ($resultText | Out-String) | ConvertFrom-Json

Write-Host ''
Write-Host 'Stopped (database kept)' -ForegroundColor Green
Write-Host ('Root: ' + $result.root)
if ($result.stopped_roles) {
    Write-Host ('Roles: ' + (($result.stopped_roles) -join ', '))
}

$result | ConvertTo-Json